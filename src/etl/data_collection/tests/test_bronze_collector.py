"""
Unit tests for the BronzeCollector object graph (FEATURE-008, review H2).

All tests run against the injected in-memory fakes from ``common`` plus a
local FakeApiClient — no AWS, no network, no module-level patching.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Tuple
from unittest.mock import Mock, patch

import pytest

# src/etl on sys.path for `common`, data_collection for flat imports.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from common.metrics_publisher import InMemoryMetricsPublisher  # noqa: E402
from common.object_store import InMemoryObjectStore  # noqa: E402
from common.secrets_provider import (  # noqa: E402
    InMemorySecretsProvider,
    SecretError,
)

from bronze_collector import (  # noqa: E402
    BronzeCollector,
    IdealistaAPIError,
    IdealistaApiClient,
    SearchConfig,
)
from common.search_config import IDEALISTA_SEARCH_PARAMS  # noqa: E402

# ---------------------------------------------------------------------------
# Test doubles + builder
# ---------------------------------------------------------------------------

SECRETS = {
    "secret-lvw": {"api_key": "lvw-key", "api_secret": "lvw-secret"},
    "secret-pmv": {"api_key": "pmv-key", "api_secret": "pmv-secret"},
}


class FakeApiClient:
    """SearchApiClient fake returning a canned response per call."""

    def __init__(self, total_pages: int = 1) -> None:
        self._total_pages = total_pages
        #: (api_key, url) per fetch_page call, for assertions.
        self.calls: List[Tuple[str, str]] = []

    def fetch_page(self, api_key: str, api_secret: str, url: str) -> str:
        self.calls.append((api_key, url))
        return json.dumps(
            {
                "total": self._total_pages * 50,
                "totalPages": self._total_pages,
                "elementList": [{"propertyCode": f"P{len(self.calls)}"}],
            }
        )


class BrokenJsonApiClient:
    """SearchApiClient fake producing an unparseable body."""

    def fetch_page(self, api_key: str, api_secret: str, url: str) -> str:
        return "<html>not json</html>"


def make_collector(
    api_client: object | None = None,
    store: InMemoryObjectStore | None = None,
    secrets: Dict[str, Dict[str, str]] | None = None,
    metrics_publisher: InMemoryMetricsPublisher | None = None,
) -> BronzeCollector:
    """Build a BronzeCollector wired entirely with fakes."""
    return BronzeCollector(
        object_store=store if store is not None else InMemoryObjectStore(),
        secrets_provider=InMemorySecretsProvider(
            secrets if secrets is not None else SECRETS
        ),
        api_client=api_client if api_client is not None else FakeApiClient(),
        secret_name_lvw="secret-lvw",
        secret_name_pmv="secret-pmv",
        s3_prefix="bronze/idealista/",
        metrics_publisher=metrics_publisher,
    )


# ---------------------------------------------------------------------------
# SearchConfig strategies
# ---------------------------------------------------------------------------


class TestSearchConfigStrategies:
    """The sale/rent variants are interchangeable Strategy objects."""

    def test_sale_factory_builds_sale_url(self) -> None:
        config = SearchConfig.sale()
        assert config.operation == "sale"
        assert "operation=sale" in config.build_url()

    def test_rent_factory_builds_rent_url(self) -> None:
        config = SearchConfig.rent()
        assert config.operation == "rent"
        assert "operation=rent" in config.build_url()

    def test_build_url_keeps_explicit_operation_override(self) -> None:
        """Backward compatibility: the old positional call style still works."""
        url = SearchConfig().build_url("rent")
        assert "operation=rent" in url
        assert "numPage=%s" in url


# ---------------------------------------------------------------------------
# Shared search-config source of truth (FEATURE-011, task 11.1)
# ---------------------------------------------------------------------------


class TestSharedSearchConfigSourceOfTruth:
    """SearchConfig must build its request from common.search_config, not
    from duplicated literals."""

    def test_build_url_reflects_shared_center_and_distance(self) -> None:
        url = SearchConfig.sale().build_url()

        lat = IDEALISTA_SEARCH_PARAMS["center_lat"]
        lon = IDEALISTA_SEARCH_PARAMS["center_lon"]
        assert f"center={lat},{lon}" in url
        assert f"distance={IDEALISTA_SEARCH_PARAMS['distance_m']}" in url

    def test_build_url_reflects_shared_size_and_filters(self) -> None:
        url = SearchConfig.rent().build_url()

        assert f"propertyType={IDEALISTA_SEARCH_PARAMS['property_type']}" in url
        assert f"minSize={IDEALISTA_SEARCH_PARAMS['min_size_m2']}" in url
        assert f"maxSize={IDEALISTA_SEARCH_PARAMS['max_size_m2']}" in url
        assert f"elevator={str(IDEALISTA_SEARCH_PARAMS['elevator']).lower()}" in url
        assert f"preservation={IDEALISTA_SEARCH_PARAMS['preservation']}" in url

    def test_build_url_actually_reads_the_shared_module(self) -> None:
        """
        Proves SearchConfig consumes ``common.search_config`` at
        construction time rather than merely duplicating equal literals.
        """
        import common.search_config as search_config_module

        original = dict(search_config_module.IDEALISTA_SEARCH_PARAMS)
        try:
            search_config_module.IDEALISTA_SEARCH_PARAMS["distance_m"] = 9999
            search_config_module.IDEALISTA_SEARCH_PARAMS["min_size_m2"] = 42
            url = SearchConfig.sale().build_url()
            assert "distance=9999" in url
            assert "minSize=42" in url
        finally:
            search_config_module.IDEALISTA_SEARCH_PARAMS.clear()
            search_config_module.IDEALISTA_SEARCH_PARAMS.update(original)

    def test_url_unchanged_from_pre_refactor_snapshot(self) -> None:
        """
        Locks the exact request shape the Idealista API has always
        received — the refactor must not silently change the payload.
        """
        url = SearchConfig().build_url("sale")
        assert url == (
            "https://api.idealista.com/3.5/es/search"
            "?operation=sale"
            "&maxItems=50"
            "&order=distance"
            "&center=39.4693441,-0.379561"
            "&distance=1500"
            "&propertyType=homes"
            "&sort=asc"
            "&minSize=100"
            "&maxSize=160"
            "&numPage=%s"
            "&elevator=true"
            "&preservation=good"
            "&language=en"
        )


# ---------------------------------------------------------------------------
# collect() — fetch → parse → persist with fakes only
# ---------------------------------------------------------------------------


class TestCollect:
    """Behaviour of the full collection run against injected fakes."""

    def test_collect_persists_all_pages_per_operation(self) -> None:
        store = InMemoryObjectStore()
        api = FakeApiClient(total_pages=3)

        result = make_collector(api_client=api, store=store).collect()

        assert result.sale_pages == 3
        assert result.rent_pages == 3
        keys = store.list_keys("bronze/idealista/")
        assert len(keys) == 6
        assert all(k.endswith(".json") for k in keys)
        assert store.content_type_of(keys[0]) == "application/json"

    def test_collect_uses_per_operation_credentials(self) -> None:
        api = FakeApiClient()

        make_collector(api_client=api).collect()

        sale_calls = [key for key, url in api.calls if "operation=sale" in url]
        rent_calls = [key for key, url in api.calls if "operation=rent" in url]
        assert sale_calls == ["lvw-key"]  # sale → LVW credentials
        assert rent_calls == ["pmv-key"]  # rent → PMV credentials

    def test_test_mode_collects_single_page_per_operation(self) -> None:
        api = FakeApiClient(total_pages=5)

        result = make_collector(api_client=api).collect(test_mode=True)

        assert result.sale_pages == 1
        assert result.rent_pages == 1

    def test_result_carries_feature_007_summary_fields(self) -> None:
        """The orchestrator summary contract fields must be populated."""
        result = make_collector(api_client=FakeApiClient(total_pages=2)).collect()

        assert result.sale_pages == 2
        assert result.rent_pages == 2
        assert result.duration_seconds >= 0.0
        assert result.total_size_mb > 0.0
        assert "2 sale pages, 2 rent pages" in result.message

    def test_invalid_json_response_raises_idealista_error(self) -> None:
        collector = make_collector(api_client=BrokenJsonApiClient())

        with pytest.raises(IdealistaAPIError, match="Invalid JSON response"):
            collector.collect()

    def test_missing_secret_raises_secret_error(self) -> None:
        collector = make_collector(secrets={})

        with pytest.raises(SecretError):
            collector.collect()

    def test_store_failure_raises_idealista_error(self) -> None:
        class ExplodingStore(InMemoryObjectStore):
            def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
                raise RuntimeError("disk full")

        collector = make_collector(store=ExplodingStore())

        with pytest.raises(IdealistaAPIError, match="Failed to upload to S3"):
            collector.collect()


# ---------------------------------------------------------------------------
# API-quota attempt metrics (FEATURE-012, task 12.3, review finding H1)
# ---------------------------------------------------------------------------


class TestApiRequestMetrics:
    """
    Every ATTEMPTED Idealista search page request must emit a metric —
    including pages that later fail to parse or persist (review H1: the
    quota light must never show a false green from an uncounted failure).
    """

    def test_every_attempted_sale_page_emits_lvw_credential_set(self) -> None:
        metrics = InMemoryMetricsPublisher()
        api = FakeApiClient(total_pages=3)

        make_collector(api_client=api, metrics_publisher=metrics).collect()

        sale_points = [
            d for d in metrics.datapoints if d.dimensions.get("Operation") == "sale"
        ]
        assert len(sale_points) == 3
        assert all(d.dimensions["CredentialSet"] == "LVW" for d in sale_points)
        assert all(d.namespace == "VlcRealEstate/Idealista" for d in sale_points)
        assert all(d.metric_name == "ApiRequests" for d in sale_points)

    def test_every_attempted_rent_page_emits_pmv_credential_set(self) -> None:
        metrics = InMemoryMetricsPublisher()
        api = FakeApiClient(total_pages=2)

        make_collector(api_client=api, metrics_publisher=metrics).collect()

        rent_points = [
            d for d in metrics.datapoints if d.dimensions.get("Operation") == "rent"
        ]
        assert len(rent_points) == 2
        assert all(d.dimensions["CredentialSet"] == "PMV" for d in rent_points)

    def test_failed_page_after_a_successful_fetch_still_records_the_attempt(
        self,
    ) -> None:
        """
        The fetch for page 2 succeeds (an API request was attempted and
        consumed quota) but persistence then fails. The attempt metric
        must still be recorded — this is the exact scenario review H1
        flagged as previously uncounted.
        """
        metrics = InMemoryMetricsPublisher()

        class FailOnSecondPageStore(InMemoryObjectStore):
            def __init__(self) -> None:
                super().__init__()
                self._calls = 0

            def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
                self._calls += 1
                if self._calls == 2:
                    raise RuntimeError("disk full")
                super().put_bytes(key, data, content_type)

        api = FakeApiClient(total_pages=3)
        collector = make_collector(
            api_client=api, store=FailOnSecondPageStore(), metrics_publisher=metrics
        )

        with pytest.raises(IdealistaAPIError, match="Failed to upload to S3"):
            collector.collect()

        # Two sale-page requests were attempted (page 1 persisted, page 2
        # failed to persist) before the run aborted — both must be counted.
        sale_points = [
            d for d in metrics.datapoints if d.dimensions.get("Operation") == "sale"
        ]
        assert len(sale_points) == 2

    def test_invalid_json_page_still_records_the_attempt(self) -> None:
        """A page whose body fails to parse was still an attempted request."""
        metrics = InMemoryMetricsPublisher()
        collector = make_collector(
            api_client=BrokenJsonApiClient(), metrics_publisher=metrics
        )

        with pytest.raises(IdealistaAPIError, match="Invalid JSON response"):
            collector.collect()

        assert len(metrics.datapoints) == 1
        assert metrics.datapoints[0].dimensions["CredentialSet"] == "LVW"

    def test_metrics_publisher_is_optional_and_defaults_to_a_no_op(self) -> None:
        """Omitting metrics_publisher must not break existing callers."""
        result = make_collector(metrics_publisher=None).collect()

        assert result.sale_pages == 1
        assert result.rent_pages == 1


# ---------------------------------------------------------------------------
# IdealistaApiClient — requests adapter (HTTP mocked)
# ---------------------------------------------------------------------------


class TestIdealistaApiClient:
    """The requests adapter: OAuth handshake + page fetch."""

    @patch("requests.post")
    def test_fetch_page_returns_body_after_token_handshake(
        self, mock_post: Mock
    ) -> None:
        token_response = Mock()
        token_response.json.return_value = {"access_token": "tok-1"}
        token_response.raise_for_status = Mock()

        page_response = Mock()
        page_response.text = '{"elementList": []}'
        page_response.raise_for_status = Mock()

        mock_post.side_effect = [token_response, page_response]

        body = IdealistaApiClient().fetch_page("k", "s", "https://api/search")

        assert body == '{"elementList": []}'
        assert mock_post.call_count == 2
        # Second call must carry the bearer token from the first.
        page_headers = mock_post.call_args_list[1].kwargs["headers"]
        assert page_headers["Authorization"] == "Bearer tok-1"

    @patch("requests.post")
    def test_empty_body_raises_rate_limit_error(self, mock_post: Mock) -> None:
        token_response = Mock()
        token_response.json.return_value = {"access_token": "tok-1"}
        token_response.raise_for_status = Mock()

        empty_response = Mock()
        empty_response.text = ""
        empty_response.raise_for_status = Mock()

        mock_post.side_effect = [token_response, empty_response]

        with pytest.raises(IdealistaAPIError, match="Empty response"):
            IdealistaApiClient().fetch_page("k", "s", "https://api/search")

    @patch("requests.post")
    def test_token_failure_raises_idealista_error(self, mock_post: Mock) -> None:
        from requests.exceptions import RequestException

        mock_post.side_effect = RequestException("network down")

        with pytest.raises(IdealistaAPIError, match="Failed to obtain OAuth token"):
            IdealistaApiClient().fetch_page("k", "s", "https://api/search")
