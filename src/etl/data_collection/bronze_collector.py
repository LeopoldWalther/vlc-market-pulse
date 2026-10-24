"""
Bronze-layer collector for Idealista listings (FEATURE-008 OOP refactor).

Class responsibilities (SOLID):

- :class:`SearchConfig` — **Strategy**: encapsulates the Idealista search
  parameters for one operation (sale/rent); variants are interchangeable.
- :class:`IdealistaApiClient` — **Adapter** around ``requests``: OAuth token
  handshake + page fetch; the only module that speaks HTTP.
- :class:`BronzeCollector` — orchestrates one weekly collection run via the
  **Template Method** skeleton fetch → parse → persist, depending only on
  the injected edge Protocols (**Dependency Inversion**): ``ObjectStore``,
  ``SecretsProvider`` and :class:`SearchApiClient`.

Notifications are deliberately NOT sent from here: the Step Functions
orchestrator publishes one per-environment summary email per run that
already includes the bronze page counts, size and duration.

boto3 never appears here — AWS is reached exclusively through the
``common`` adapters wired in by the handler's Factory.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Protocol, Tuple

from common.metrics_publisher import MetricsPublisher
from common.object_store import ObjectStore
from common.search_config import IDEALISTA_SEARCH_PARAMS
from common.secrets_provider import SecretsProvider

logger = logging.getLogger()

# Constants (unchanged from the pre-refactor module)
API_TIMEOUT_SECONDS = 30
OAUTH_TOKEN_URL = "https://api.idealista.com/oauth/token"

# FEATURE-012 (task 12.3): API-quota instrumentation constants.
METRICS_NAMESPACE = "VlcRealEstate/Idealista"
API_REQUESTS_METRIC_NAME = "ApiRequests"

#: Credential-set labels the review (finding H1) requires explicitly:
#: LVW = sale, PMV = rent.
CREDENTIAL_SET_BY_OPERATION = {"sale": "LVW", "rent": "PMV"}


class NoOpMetricsPublisher:
    """
    :class:`~common.metrics_publisher.MetricsPublisher` no-op default.

    Keeps ``metrics_publisher`` an optional collaborator (existing
    callers/tests that predate FEATURE-012 keep working unchanged)
    without sprinkling ``if self._metrics is not None`` checks through
    the collection loop (None Object pattern).
    """

    def publish(
        self,
        namespace: str,
        metric_name: str,
        value: float,
        unit: str,
        dimensions: Dict[str, str],
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Discard the datapoint; no metrics backend configured."""


class IdealistaAPIError(Exception):
    """Custom exception for Idealista API errors."""


class SearchConfig:
    """
    Strategy: Idealista search parameters for a single operation.

    Extends the original value object (review L2 — promoted, not replaced):
    each instance now carries its ``operation`` so sale/rent variants are
    interchangeable strategies. ``build_url`` keeps its optional
    *operation* argument for backward compatibility.

    FEATURE-011 (task 11.1): every search literal is read from the shared
    :data:`common.search_config.IDEALISTA_SEARCH_PARAMS` constant at
    construction time, so the collector and the gold ``data_basis.search_config``
    dataset can never silently drift apart (review M2). The Idealista API
    request payload is unchanged.
    """

    BASE_URL = IDEALISTA_SEARCH_PARAMS["base_url"]
    COUNTRY = IDEALISTA_SEARCH_PARAMS["country"]

    def __init__(self, operation: str = "sale") -> None:
        """
        Args:
            operation: Either ``'sale'`` or ``'rent'``.
        """
        params = IDEALISTA_SEARCH_PARAMS
        self.operation = operation
        self.max_items = str(params["max_items"])
        self.order = params["order"]
        self.center = f"{params['center_lat']},{params['center_lon']}"
        self.distance = str(params["distance_m"])
        self.property_type = params["property_type"]
        self.sort = params["sort"]
        self.min_size = str(params["min_size_m2"])
        self.max_size = str(params["max_size_m2"])
        self.elevator = str(params["elevator"]).lower()
        self.air_conditioning = str(params["air_conditioning"]).lower()
        self.preservation = params["preservation"]
        self.language = params["language"]

    @classmethod
    def sale(cls) -> "SearchConfig":
        """Factory: the sale-operation strategy variant."""
        return cls(operation="sale")

    @classmethod
    def rent(cls) -> "SearchConfig":
        """Factory: the rent-operation strategy variant."""
        return cls(operation="rent")

    def build_url(self, operation: Optional[str] = None) -> str:
        """
        Build the search URL with a ``%s`` placeholder for the page number.

        Args:
            operation: Optional override; defaults to the instance's own
                operation (kept for backward compatibility).

        Returns:
            Formatted URL string with placeholder for page number.
        """
        op = operation if operation is not None else self.operation
        return (
            f"{self.BASE_URL}{self.COUNTRY}/search"
            f"?operation={op}"
            f"&maxItems={self.max_items}"
            f"&order={self.order}"
            f"&center={self.center}"
            f"&distance={self.distance}"
            f"&propertyType={self.property_type}"
            f"&sort={self.sort}"
            f"&minSize={self.min_size}"
            f"&maxSize={self.max_size}"
            f"&numPage=%s"
            f"&elevator={self.elevator}"
            # f"&airConditioning={self.air_conditioning}"
            f"&preservation={self.preservation}"
            f"&language={self.language}"
        )


class SearchApiClient(Protocol):
    """
    Narrow HTTP edge interface (Interface Segregation).

    One operation: fetch a single result page as raw JSON text. The
    collector never sees tokens, headers or the HTTP library.
    """

    def fetch_page(self, api_key: str, api_secret: str, url: str) -> str:
        """Return the raw JSON response body for *url*."""
        ...


class IdealistaApiClient:
    """
    requests-backed :class:`SearchApiClient` adapter for the Idealista API.

    Adapter pattern: wraps the OAuth2 handshake and the search POST behind
    the project-owned Protocol so the collector core stays vendor-neutral.
    """

    def __init__(
        self,
        token_url: str = OAUTH_TOKEN_URL,
        timeout_seconds: int = API_TIMEOUT_SECONDS,
    ) -> None:
        """
        Args:
            token_url: OAuth2 token endpoint.
            timeout_seconds: Timeout applied to every HTTP call.
        """
        self._token_url = token_url
        self._timeout = timeout_seconds

    def _get_oauth_token(self, api_key: str, api_secret: str) -> str:
        """
        Obtain an OAuth bearer token from the Idealista API.

        Raises:
            IdealistaAPIError: If the token cannot be obtained.
        """
        # requests is imported inside the adapter so core logic never
        # depends on the HTTP library (Dependency Inversion at the edge).
        import requests

        try:
            message = f"{api_key}:{api_secret}"
            auth_header = "Basic " + base64.b64encode(message.encode("ascii")).decode(
                "ascii"
            )

            headers = {
                "Authorization": auth_header,
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            }
            params = {"grant_type": "client_credentials", "scope": "read"}

            response = requests.post(
                self._token_url,
                headers=headers,
                params=params,
                timeout=self._timeout,
            )
            response.raise_for_status()

            return str(response.json()["access_token"])
        except (requests.RequestException, KeyError) as e:
            logger.error(f"Error obtaining OAuth token: {e}")
            raise IdealistaAPIError(f"Failed to obtain OAuth token: {e}")

    def fetch_page(self, api_key: str, api_secret: str, url: str) -> str:
        """
        Query one search page and return the raw JSON body.

        Raises:
            IdealistaAPIError: On HTTP failure or an empty body (rate limit).
        """
        import requests

        try:
            token = self._get_oauth_token(api_key, api_secret)
            headers = {
                "Content-Type": "Content-Type: multipart/form-data;",
                "Authorization": f"Bearer {token}",
            }

            response = requests.post(url, headers=headers, timeout=self._timeout)
            response.raise_for_status()

            if not response.text:
                raise IdealistaAPIError(
                    "Empty response from API - may have exceeded rate limit"
                )

            return response.text
        except requests.RequestException as e:
            logger.error(f"Error querying API: {e}")
            raise IdealistaAPIError(f"Failed to query API: {e}")


@dataclass(frozen=True)
class CollectionResult:
    """Immutable summary of one collection run (feeds the response body)."""

    timestamp: str
    sale_pages: int
    rent_pages: int
    duration_seconds: float
    total_size_mb: float

    @property
    def message(self) -> str:
        """Human-readable one-line summary."""
        return (
            f"Successfully collected listings: "
            f"{self.sale_pages} sale pages, {self.rent_pages} rent pages"
        )


class BronzeCollector:
    """
    Orchestrates one weekly bronze collection run.

    Template Method: :meth:`collect` fixes the skeleton — credentials →
    per-operation fetch/parse/persist → metrics — while the variable parts
    (search parameters, HTTP transport, storage, secrets) are supplied as injected collaborators
    (Dependency Injection / Single Responsibility).

    CRITICAL: the result fields feed the Lambda response body that
    FEATURE-007's ExtractSummary state parses (sale/rent page counts,
    size, duration) — their names and semantics must not change.
    """

    def __init__(
        self,
        *,
        object_store: ObjectStore,
        secrets_provider: SecretsProvider,
        api_client: SearchApiClient,
        secret_name_lvw: str,
        secret_name_pmv: str,
        s3_prefix: str = "bronze/idealista/",
        sale_config: Optional[SearchConfig] = None,
        rent_config: Optional[SearchConfig] = None,
        metrics_publisher: Optional[MetricsPublisher] = None,
    ) -> None:
        """
        Args:
            object_store: Storage edge for the bronze JSON pages.
            secrets_provider: Source of the two API credential sets.
            api_client: HTTP edge for the Idealista API.
            secret_name_lvw: Secret name for the LVW credential set (sale).
            secret_name_pmv: Secret name for the PMV credential set (rent).
            s3_prefix: Key prefix for bronze objects.
            sale_config: Sale search strategy (defaults to
                :meth:`SearchConfig.sale`).
            rent_config: Rent search strategy (defaults to
                :meth:`SearchConfig.rent`).
            metrics_publisher: Optional (FEATURE-012, task 12.3) edge for
                publishing per-attempt ``ApiRequests`` quota metrics.
                Defaults to a no-op publisher so existing callers that
                predate this feature keep working unchanged.
        """
        self._object_store = object_store
        self._secrets = secrets_provider
        self._api = api_client
        self._secret_name_lvw = secret_name_lvw
        self._secret_name_pmv = secret_name_pmv
        self._s3_prefix = s3_prefix
        self._sale_config = sale_config or SearchConfig.sale()
        self._rent_config = rent_config or SearchConfig.rent()
        self._metrics = metrics_publisher or NoOpMetricsPublisher()

    def collect(self, test_mode: bool = False) -> CollectionResult:
        """
        Run the full collection: sale + rent, persist all pages.

        Args:
            test_mode: When ``True``, collect only 1 page per operation
                (dev behaviour, stays within the Idealista API quota).

        Returns:
            The run summary consumed by the Lambda response body.

        Raises:
            IdealistaAPIError: On credential, API or storage failures.
        """
        start_time = datetime.now()
        max_pages = 1 if test_mode else None

        if test_mode:
            logger.info("Running in TEST MODE - will only process 1 page per operation")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        logger.info("Retrieving credentials from Secrets Manager")
        credentials_lvw = self._secrets.get_secret(self._secret_name_lvw)
        credentials_pmv = self._secrets.get_secret(self._secret_name_pmv)

        logger.info("Processing sale listings")
        sale_pages, sale_bytes = self._collect_operation(
            config=self._sale_config,
            credentials=credentials_lvw,
            timestamp=timestamp,
            max_pages=max_pages,
        )

        logger.info("Processing rent listings")
        rent_pages, rent_bytes = self._collect_operation(
            config=self._rent_config,
            credentials=credentials_pmv,
            timestamp=timestamp,
            max_pages=max_pages,
        )

        duration_seconds = (datetime.now() - start_time).total_seconds()
        total_size_mb = (sale_bytes + rent_bytes) / (1024 * 1024)

        result = CollectionResult(
            timestamp=timestamp,
            sale_pages=sale_pages,
            rent_pages=rent_pages,
            duration_seconds=duration_seconds,
            total_size_mb=total_size_mb,
        )
        logger.info(result.message)
        return result

    def _collect_operation(
        self,
        config: SearchConfig,
        credentials: Dict[str, str],
        timestamp: str,
        max_pages: Optional[int],
    ) -> Tuple[int, int]:
        """
        Fetch → parse → persist every page of one operation.

        Args:
            config: Search strategy for this operation.
            credentials: ``api_key`` / ``api_secret`` mapping.
            timestamp: Run timestamp used in the object keys.
            max_pages: Page cap (test mode) or ``None`` for all pages.

        Returns:
            Tuple of (pages persisted, total bytes persisted).

        Raises:
            IdealistaAPIError: On fetch, parse or persist failure.
        """
        url_template = config.build_url()
        operation = config.operation

        page = 1
        total_pages = 1  # Updated from the first API response.
        bytes_written = 0

        while page <= total_pages:
            if max_pages is not None and page > max_pages:
                logger.info(f"Reached max_pages limit ({max_pages}), stopping")
                break

            url = url_template % page
            logger.info(f"Processing {operation} page {page}/{total_pages}")

            # FEATURE-012 (task 12.3, review H1): the metric must count
            # every ATTEMPTED search request, including ones that later
            # fail to parse or persist. The `finally` block below runs
            # exactly once per page regardless of how this try block
            # exits, so quota pressure is never silently under-reported.
            try:
                # Fetch
                response_json = self._api.fetch_page(
                    credentials["api_key"], credentials["api_secret"], url
                )

                # Parse — only to learn totalPages; raw body is persisted.
                try:
                    response_data = json.loads(response_json)
                except json.JSONDecodeError as e:
                    logger.error(
                        f"Error parsing JSON response for {operation} page {page}: {e}"
                    )
                    raise IdealistaAPIError(f"Invalid JSON response: {e}")
                total_pages = response_data.get("totalPages", total_pages)

                # Persist
                key = f"{self._s3_prefix}{operation}_{timestamp}_{page}.json"
                data = response_json.encode("utf-8")
                try:
                    self._object_store.put_bytes(key, data, "application/json")
                except Exception as e:
                    logger.error(f"Error uploading to S3: {e}")
                    raise IdealistaAPIError(f"Failed to upload to S3: {e}")
                bytes_written += len(data)
            finally:
                self._publish_attempt_metric(operation)

            page += 1

        pages_processed = page - 1
        logger.info(
            f"Completed {operation} operation: "
            f"{pages_processed} pages written to S3"
        )
        return pages_processed, bytes_written

    def _publish_attempt_metric(self, operation: str) -> None:
        """
        Emit one ``ApiRequests`` datapoint for an attempted search page.

        Called once per page from a ``finally`` block (task 12.3, review
        H1) so failed/partial pages are counted exactly like successful
        ones. Failures from the metrics backend must never fail the
        collection run.

        Args:
            operation: ``'sale'`` or ``'rent'`` — mapped to its
                credential-set label (LVW/PMV).
        """
        try:
            self._metrics.publish(
                namespace=METRICS_NAMESPACE,
                metric_name=API_REQUESTS_METRIC_NAME,
                value=1.0,
                unit="Count",
                dimensions={
                    "CredentialSet": CREDENTIAL_SET_BY_OPERATION[operation],
                    "Operation": operation,
                },
            )
        except Exception as exc:  # noqa: BLE001 — never fail the run
            logger.error(f"Error publishing API request metric: {exc}")


__all__: List[str] = [
    "API_REQUESTS_METRIC_NAME",
    "API_TIMEOUT_SECONDS",
    "BronzeCollector",
    "CollectionResult",
    "CREDENTIAL_SET_BY_OPERATION",
    "IdealistaAPIError",
    "IdealistaApiClient",
    "METRICS_NAMESPACE",
    "NoOpMetricsPublisher",
    "OAUTH_TOKEN_URL",
    "SearchApiClient",
    "SearchConfig",
]
