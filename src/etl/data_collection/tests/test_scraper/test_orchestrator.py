"""
Unit tests for the ScrapeOrchestrator (FEATURE-002, task 1.9).

All five collaborators are fakes/mocks — no network, no AWS, no real
sleeping — asserting: pagination stops at the empty page, proxy
rotation + delay between pages, and one repository.save() call per
non-empty page.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from data_collection.scraper.domain import Listing, ListingCollection
from data_collection.scraper.orchestrator import ScrapeOrchestrator
from data_collection.scraper.urls import SaleStrategy


class _FakeFetcher:
    """Returns a scripted HTML string per URL call (order-based)."""

    def __init__(self, html_per_call: List[str]) -> None:
        self._html_per_call = list(html_per_call)
        self.urls: List[str] = []

    def fetch(self, url: str) -> str:
        self.urls.append(url)
        if not self._html_per_call:
            return ""
        return self._html_per_call.pop(0)


class _FakeParser:
    """Returns a scripted ListingCollection per call (order-based)."""

    def __init__(self, listings_per_page: List[List[Listing]]) -> None:
        self._listings_per_page = list(listings_per_page)
        self.calls: List[Tuple[str, str]] = []

    def parse(self, html: str, operation: str) -> ListingCollection:
        self.calls.append((html, operation))
        listings = self._listings_per_page.pop(0) if self._listings_per_page else []
        return ListingCollection(listings)


class _FakeRepository:
    def __init__(self) -> None:
        self.saved: List[Dict[str, Any]] = []

    def save(
        self,
        envelope: Dict[str, Any],
        operation: str,
        page: int,
        date: Optional[str] = None,
    ) -> str:
        self.saved.append({"envelope": envelope, "operation": operation, "page": page})
        return f"fake://{operation}/{page}"


class _FakeProxyProvider:
    def __init__(self) -> None:
        self.rotations = 0

    def get_proxy(self) -> None:
        return None

    def rotate(self) -> None:
        self.rotations += 1


def _listing(code: str) -> Listing:
    return Listing(
        property_code=code,
        price=100000,
        size=50,
        rooms=2,
        bathrooms=1,
        address="Somewhere",
        url=f"https://www.idealista.com/inmueble/{code}/",
        operation="sale",
    )


class TestScrapeOrchestratorPagination:
    def test_stops_at_empty_page(self) -> None:
        fetcher = _FakeFetcher(["<html>1</html>", "<html>2</html>", "<html>3</html>"])
        parser = _FakeParser([[_listing("1")], [_listing("2")], []])
        repository = _FakeRepository()
        proxy_provider = _FakeProxyProvider()
        sleep_calls: List[float] = []
        random_calls: List[Tuple[float, float]] = []

        orchestrator = ScrapeOrchestrator(
            fetcher=fetcher,
            parser=parser,
            repository=repository,
            proxy_provider=proxy_provider,
            sleep_fn=sleep_calls.append,
            random_fn=lambda lo, hi: random_calls.append((lo, hi)) or 3.0,
        )

        result = orchestrator.scrape(SaleStrategy())

        assert len(fetcher.urls) == 3  # page 1, 2, 3 (empty stops it)
        assert len(result) == 2

    def test_persists_each_non_empty_page(self) -> None:
        fetcher = _FakeFetcher(["<html>1</html>", "<html>2</html>"])
        parser = _FakeParser([[_listing("1")], []])
        repository = _FakeRepository()
        proxy_provider = _FakeProxyProvider()

        orchestrator = ScrapeOrchestrator(
            fetcher=fetcher,
            parser=parser,
            repository=repository,
            proxy_provider=proxy_provider,
            sleep_fn=lambda _: None,
            random_fn=lambda lo, hi: 3.0,
        )
        orchestrator.scrape(SaleStrategy())

        assert len(repository.saved) == 1
        assert repository.saved[0]["operation"] == "sale"
        assert repository.saved[0]["page"] == 1

    def test_rotates_proxy_between_pages(self) -> None:
        fetcher = _FakeFetcher(["<html>1</html>", "<html>2</html>", "<html>3</html>"])
        parser = _FakeParser([[_listing("1")], [_listing("2")], []])
        repository = _FakeRepository()
        proxy_provider = _FakeProxyProvider()

        orchestrator = ScrapeOrchestrator(
            fetcher=fetcher,
            parser=parser,
            repository=repository,
            proxy_provider=proxy_provider,
            sleep_fn=lambda _: None,
            random_fn=lambda lo, hi: 3.0,
        )
        orchestrator.scrape(SaleStrategy())

        # Rotated once per non-empty page fetched (2 non-empty pages).
        assert proxy_provider.rotations == 2

    def test_sleeps_a_value_within_delay_range(self) -> None:
        fetcher = _FakeFetcher(["<html>1</html>", "<html>2</html>"])
        parser = _FakeParser([[_listing("1")], []])
        repository = _FakeRepository()
        proxy_provider = _FakeProxyProvider()
        sleep_calls: List[float] = []

        orchestrator = ScrapeOrchestrator(
            fetcher=fetcher,
            parser=parser,
            repository=repository,
            proxy_provider=proxy_provider,
            sleep_fn=sleep_calls.append,
            random_fn=lambda lo, hi: (lo + hi) / 2,
        )
        orchestrator.scrape(SaleStrategy())

        assert sleep_calls == [pytest.approx((2.0 + 4.5) / 2)]

    def test_returns_union_of_all_pages(self) -> None:
        fetcher = _FakeFetcher(["<html>1</html>", "<html>2</html>", "<html>3</html>"])
        parser = _FakeParser([[_listing("1")], [_listing("2"), _listing("3")], []])
        repository = _FakeRepository()
        proxy_provider = _FakeProxyProvider()

        orchestrator = ScrapeOrchestrator(
            fetcher=fetcher,
            parser=parser,
            repository=repository,
            proxy_provider=proxy_provider,
            sleep_fn=lambda _: None,
            random_fn=lambda lo, hi: 3.0,
        )
        result = orchestrator.scrape(SaleStrategy())

        codes = {listing.property_code for listing in result}
        assert codes == {"1", "2", "3"}

    def test_empty_first_page_yields_empty_result_and_no_save(self) -> None:
        fetcher = _FakeFetcher(["<html></html>"])
        parser = _FakeParser([[]])
        repository = _FakeRepository()
        proxy_provider = _FakeProxyProvider()

        orchestrator = ScrapeOrchestrator(
            fetcher=fetcher,
            parser=parser,
            repository=repository,
            proxy_provider=proxy_provider,
            sleep_fn=lambda _: None,
            random_fn=lambda lo, hi: 3.0,
        )
        result = orchestrator.scrape(SaleStrategy())

        assert len(result) == 0
        assert repository.saved == []
        assert proxy_provider.rotations == 0
