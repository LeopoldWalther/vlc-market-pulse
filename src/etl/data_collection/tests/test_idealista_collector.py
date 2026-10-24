"""
Unit tests for the thin bronze Lambda handler (FEATURE-008).

The handler is only glue: a Factory (`build_collector`) plus response
shaping. Collection behaviour itself is covered in
``test_bronze_collector.py`` against in-memory fakes.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import idealista_listings_collector as handler_module  # noqa: E402
from bronze_collector import CollectionResult, IdealistaAPIError  # noqa: E402
from idealista_listings_collector import (  # noqa: E402
    BronzeCollector,
    build_collector,
    lambda_handler,
)

FULL_ENV: Dict[str, str] = {
    "S3_BUCKET": "test-bucket",
    "SECRET_NAME_LVW": "secret-lvw",
    "SECRET_NAME_PMV": "secret-pmv",
}

RESULT = CollectionResult(
    timestamp="20260101_120000",
    sale_pages=3,
    rent_pages=2,
    duration_seconds=1.5,
    total_size_mb=0.4567,
)


class TestBuildCollector:
    """Factory wiring and environment validation."""

    def test_builds_collector_with_full_environment(self) -> None:
        collector = build_collector(FULL_ENV)
        assert isinstance(collector, BronzeCollector)

    @pytest.mark.parametrize("missing", sorted(FULL_ENV))
    def test_missing_env_var_raises_idealista_error(self, missing: str) -> None:
        env = {k: v for k, v in FULL_ENV.items() if k != missing}

        with pytest.raises(
            IdealistaAPIError, match="Missing required environment variables"
        ):
            build_collector(env)


class TestLambdaHandler:
    """Response shaping for success and both error paths."""

    def _run(self, event: object, result: CollectionResult = RESULT) -> Dict:
        collector = Mock()
        collector.collect.return_value = result
        with patch.object(handler_module, "build_collector", return_value=collector):
            response = lambda_handler(event, None)
        self.collector = collector
        return response

    def test_success_returns_200_with_feature_007_contract_fields(self) -> None:
        response = self._run({})

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["timestamp"] == "20260101_120000"
        assert body["sale_pages"] == 3
        assert body["rent_pages"] == 2
        assert body["duration_seconds"] == 1.5
        assert body["total_size_mb"] == 0.46  # rounded to 2 decimals
        assert "3 sale pages, 2 rent pages" in body["message"]

    def test_test_mode_flag_is_forwarded_to_collect(self) -> None:
        self._run({"test_mode": True})
        self.collector.collect.assert_called_once_with(test_mode=True)

    def test_non_dict_event_defaults_to_normal_mode(self) -> None:
        self._run(None)
        self.collector.collect.assert_called_once_with(test_mode=False)

    def test_idealista_error_returns_500_with_message(self) -> None:
        collector = Mock()
        collector.collect.side_effect = IdealistaAPIError("API quota exceeded")
        with patch.object(handler_module, "build_collector", return_value=collector):
            response = lambda_handler({}, None)

        assert response["statusCode"] == 500
        assert json.loads(response["body"])["error"] == "API quota exceeded"

    def test_unexpected_error_returns_500_with_prefix(self) -> None:
        with patch.object(
            handler_module, "build_collector", side_effect=RuntimeError("boom")
        ):
            response = lambda_handler({}, None)

        assert response["statusCode"] == 500
        assert json.loads(response["body"])["error"] == "Unexpected error: boom"
