"""
Page fetching with retry/backoff for the Idealista web scraper (FEATURE-002, task 1.5).

:class:`PageFetcher` is a Template Method: :meth:`PageFetcher.fetch`
fixes the skeleton — select a proxy, transport the request, retry with
exponential backoff and proxy rotation on rate-limit/anti-bot status
codes, and finally raise :class:`~errors.FetchError` once the retry
budget is exhausted. Only the transport step (``_transport``) varies
per concrete subclass.

:class:`CloudscraperFetcher` is today's transport: it uses the
``cloudscraper`` library (a ``requests``-compatible session that can
solve some Cloudflare "I'm Under Attack" JS challenges) with a
realistic User-Agent. Because every Cloudflare/HTTP-library concern is
confined to this module (per REVIEW-FEATURE-002 finding M3), swapping
in a headless-browser fetcher later (e.g. Playwright) is a drop-in
replacement: implement a new ``PageFetcher`` subclass, nothing else in
the codebase changes.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Callable, Dict, Optional, Protocol

from .errors import FetchError
from .proxies import ProxyProvider

logger = logging.getLogger(__name__)

#: HTTP status codes that indicate rate-limiting or anti-bot blocking,
#: worth retrying (with a fresh proxy) rather than failing immediately.
RETRYABLE_STATUS_CODES = frozenset({429, 403, 503})

#: A realistic desktop browser User-Agent so the request doesn't look
#: like a bare Python HTTP client.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)


class _Response(Protocol):
    """Narrow response shape the Template Method depends on."""

    status_code: int
    text: str


class PageFetcher(ABC):
    """
    Template Method: proxy selection -> transport -> retry/backoff.

    Args:
        proxy_provider: Source of the (rotating) proxy dict.
        max_retries: Maximum number of retry attempts after the first
            try (so ``max_retries=3`` means up to 4 total attempts).
        base_delay: Base delay in seconds for exponential backoff
            (``base_delay * 2 ** attempt``).
        sleep_fn: Injected sleep function (defaults to ``time.sleep``);
            overridden in tests so retries run instantly.
    """

    def __init__(
        self,
        proxy_provider: ProxyProvider,
        max_retries: int = 4,
        base_delay: float = 1.0,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._proxy_provider = proxy_provider
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._sleep_fn = sleep_fn

    def fetch(self, url: str) -> str:
        """
        Fetch *url*, retrying with backoff + proxy rotation as needed.

        Args:
            url: The absolute URL to fetch.

        Returns:
            The response body text.

        Raises:
            FetchError: If every attempt is exhausted without a
                non-retryable response, or the transport raises.
        """
        last_status: Optional[int] = None
        last_error: Optional[Exception] = None

        for attempt in range(self._max_retries + 1):
            proxy = self._proxy_provider.get_proxy()
            try:
                response = self._transport(url, proxy)
            except Exception as exc:  # noqa: BLE001 — transport errors are retryable
                last_error = exc
                last_status = None
            else:
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    if response.status_code >= 400:
                        raise FetchError(
                            f"Non-retryable HTTP {response.status_code} for {url}"
                        )
                    return response.text
                last_status = response.status_code
                last_error = None

            if attempt < self._max_retries:
                delay = self._base_delay * (2**attempt)
                logger.warning(
                    "Fetch attempt %d/%d for %s failed (status=%s, error=%s); "
                    "rotating proxy and retrying in %.1fs",
                    attempt + 1,
                    self._max_retries + 1,
                    url,
                    last_status,
                    last_error,
                    delay,
                )
                self._proxy_provider.rotate()
                self._sleep_fn(delay)

        raise FetchError(
            f"Failed to fetch {url} after {self._max_retries + 1} attempts "
            f"(last_status={last_status}, last_error={last_error})"
        )

    @abstractmethod
    def _transport(self, url: str, proxy: Optional[Dict[str, str]]) -> _Response:
        """
        Perform the actual HTTP request; the only vendor-specific step.

        Args:
            url: The absolute URL to fetch.
            proxy: The ``requests``-style proxy dict (or ``None``).

        Returns:
            An object exposing ``status_code`` and ``text``.
        """


class CloudscraperFetcher(PageFetcher):
    """
    :class:`PageFetcher` transport backed by the ``cloudscraper`` library.

    All Cloudflare/``cloudscraper``-specific code lives here; nothing
    elsewhere in the scraper package imports ``cloudscraper`` directly
    (Dependency Inversion at this edge, mirroring
    ``common/object_store.py``'s boto3 confinement).
    """

    def __init__(
        self,
        proxy_provider: ProxyProvider,
        max_retries: int = 4,
        base_delay: float = 1.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        timeout_seconds: int = 30,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        """
        Args:
            proxy_provider: Source of the (rotating) proxy dict.
            max_retries: Maximum retry attempts after the first try.
            base_delay: Base delay in seconds for exponential backoff.
            sleep_fn: Injected sleep function (test seam).
            timeout_seconds: Per-request timeout.
            user_agent: User-Agent header sent with every request.
        """
        super().__init__(proxy_provider, max_retries, base_delay, sleep_fn)
        self._timeout = timeout_seconds
        self._headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        }

    def _transport(self, url: str, proxy: Optional[Dict[str, str]]) -> _Response:
        """Fetch *url* through a ``cloudscraper`` session."""
        # cloudscraper is imported inside the adapter so the rest of the
        # package never depends on it directly (Dependency Inversion).
        import cloudscraper  # type: ignore[import-untyped]

        scraper = cloudscraper.create_scraper()
        response = scraper.get(
            url,
            headers=self._headers,
            proxies=proxy,
            timeout=self._timeout,
        )
        return response
