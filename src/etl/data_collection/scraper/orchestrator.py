"""
Scrape orchestration for the Idealista web scraper (FEATURE-002, task 1.9).

:class:`ScrapeOrchestrator` is the high-level policy object: it depends
only on the four collaborator abstractions injected into its
constructor (:class:`~fetcher.PageFetcher`, a narrow ``ListingParser``
Protocol, :class:`~repository.ListingRepository`,
:class:`~proxies.ProxyProvider`) plus :class:`~urls.IdealistaUrlBuilder`,
which it builds per :class:`~urls.OperationStrategy` inside
:meth:`scrape` — never a concrete AWS/HTTP class (Dependency Inversion).
This makes the orchestrator fully testable with fakes/mocks for all
five collaborators (no network, no AWS, no real sleeping).
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, Optional, Protocol

from .domain import ListingCollection
from .proxies import ProxyProvider
from .repository import ListingRepository
from .urls import IdealistaUrlBuilder, OperationStrategy

logger = logging.getLogger(__name__)

#: Randomised inter-page delay range (seconds) — polite-scraping default
#: called out in REVIEW-FEATURE-002 finding H2.
DELAY_RANGE_SECONDS = (2.0, 4.5)


class PageFetcherProtocol(Protocol):
    """Narrow fetch interface the orchestrator depends on."""

    def fetch(self, url: str) -> str:
        """Return the response body text for *url*."""
        ...


class ListingParserProtocol(Protocol):
    """Narrow parse interface the orchestrator depends on."""

    def parse(self, html: str, operation: str) -> ListingCollection:
        """Parse *html* into a :class:`~domain.ListingCollection`."""
        ...


class ScrapeOrchestrator:
    """
    Orchestrates one full paginated scrape for a given operation.

    :meth:`scrape` builds a URL for page 1, 2, 3, ... via
    :class:`~urls.IdealistaUrlBuilder`, fetches, parses, and persists
    each page, rotating the proxy and sleeping a randomised delay
    between pages, stopping as soon as a page yields zero listings
    (empty/last page).
    """

    def __init__(
        self,
        fetcher: PageFetcherProtocol,
        parser: ListingParserProtocol,
        repository: ListingRepository,
        proxy_provider: ProxyProvider,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[float, float], float] = random.uniform,
        delay_range: tuple[float, float] = DELAY_RANGE_SECONDS,
    ) -> None:
        """
        Args:
            fetcher: Fetches one page's raw HTML.
            parser: Parses HTML into a :class:`~domain.ListingCollection`.
            repository: Persists each parsed page's envelope.
            proxy_provider: Rotated between pages.
            sleep_fn: Injected sleep function (test seam).
            random_fn: Injected ``random.uniform``-shaped function (test seam).
            delay_range: ``(min_seconds, max_seconds)`` inter-page delay.
        """
        self._fetcher = fetcher
        self._parser = parser
        self._repository = repository
        self._proxy_provider = proxy_provider
        self._sleep_fn = sleep_fn
        self._random_fn = random_fn
        self._delay_range = delay_range

    def scrape(
        self, operation_strategy: OperationStrategy, max_pages: Optional[int] = None
    ) -> ListingCollection:
        """
        Scrape every page for *operation_strategy* until an empty page.

        Args:
            operation_strategy: :class:`~urls.SaleStrategy` or
                :class:`~urls.RentStrategy` (or any future variant).
            max_pages: Optional cap on the number of pages fetched,
                regardless of whether later pages would have listings
                (task 1.10: lets the CLI/notebook run a bounded smoke
                test instead of forcing a full-inventory scrape).

        Returns:
            The union of all listings scraped across every page.
        """
        url_builder = IdealistaUrlBuilder(operation_strategy)
        operation = operation_strategy.operation_label
        all_listings = ListingCollection()

        page = 1
        while max_pages is None or page <= max_pages:
            url = url_builder.build(page)
            logger.info("Fetching %s page %d: %s", operation, page, url)

            html = self._fetcher.fetch(url)
            page_listings = self._parser.parse(html, operation)

            if len(page_listings) == 0:
                logger.info(
                    "Page %d for %s returned no listings; stopping pagination",
                    page,
                    operation,
                )
                break

            envelope = page_listings.to_envelope(
                operation=operation, page=page, total_pages=page
            )
            self._repository.save(envelope, operation=operation, page=page)

            for listing in page_listings:
                all_listings.add(listing)

            self._proxy_provider.rotate()
            delay = self._random_fn(*self._delay_range)
            self._sleep_fn(delay)

            page += 1

        return all_listings
