"""
Unit tests for the Idealista listing parser (FEATURE-002, task 1.7).

Loads the frozen fixture from task 1.6 (no network) and asserts correct
extraction of price/size/rooms/address/url/propertyCode, plus a
missing-optional-field edge case using an inline HTML snippet.
"""

from __future__ import annotations

from pathlib import Path

from data_collection.scraper.domain import ListingCollection
from data_collection.scraper.parser import IdealistaListingParser

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "search_results_sale.html"
)


def _load_fixture() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


class TestIdealistaListingParser:
    def test_parses_all_cards_from_fixture(self) -> None:
        parser = IdealistaListingParser()
        collection = parser.parse(_load_fixture(), operation="sale")

        assert isinstance(collection, ListingCollection)
        assert len(collection) == 5

    def test_first_listing_fields(self) -> None:
        parser = IdealistaListingParser()
        collection = parser.parse(_load_fixture(), operation="sale")
        listing = next(
            listing_ for listing_ in collection if listing_.property_code == "107517743"
        )

        assert listing.price == 695000.0
        assert listing.size == 132.0
        assert listing.rooms == 3
        assert listing.floor == "2nd"
        assert listing.address is not None and "Ruzafa" in listing.address
        assert listing.url == "https://www.idealista.com/inmueble/107517743/"
        assert listing.operation == "sale"

    def test_second_listing_fields(self) -> None:
        parser = IdealistaListingParser()
        collection = parser.parse(_load_fixture(), operation="sale")
        listing = next(
            listing_ for listing_ in collection if listing_.property_code == "108234501"
        )

        assert listing.price == 245000.0
        assert listing.size == 78.0
        assert listing.rooms == 2

    def test_ground_floor_parsed(self) -> None:
        parser = IdealistaListingParser()
        collection = parser.parse(_load_fixture(), operation="sale")
        listing = next(
            listing_ for listing_ in collection if listing_.property_code == "110456789"
        )
        assert listing.floor == "Ground" or listing.floor == "ground"

    def test_operation_is_stamped_on_every_listing(self) -> None:
        parser = IdealistaListingParser()
        collection = parser.parse(_load_fixture(), operation="rent")
        assert all(listing.operation == "rent" for listing in collection)

    def test_all_property_codes_present(self) -> None:
        parser = IdealistaListingParser()
        collection = parser.parse(_load_fixture(), operation="sale")
        codes = {listing.property_code for listing in collection}
        assert codes == {
            "107517743",
            "108234501",
            "109887654",
            "110456789",
            "111222333",
        }


class TestMissingOptionalFields:
    """Selectors that don't match must yield None, never raise."""

    def test_missing_price_and_details_yield_none(self) -> None:
        html = """
        <main class="listing-items">
          <article class="item" data-element-id="999999">
            <a class="item-link" href="/inmueble/999999/">Bare listing</a>
          </article>
        </main>
        """
        parser = IdealistaListingParser()
        collection = parser.parse(html, operation="sale")

        assert len(collection) == 1
        listing = collection.to_list()[0]
        assert listing.property_code == "999999"
        assert listing.price is None
        assert listing.size is None
        assert listing.rooms is None
        assert listing.floor is None

    def test_article_without_link_or_code_is_skipped(self) -> None:
        html = """
        <main class="listing-items">
          <article class="item">
            <span class="item-price">100.000€</span>
          </article>
        </main>
        """
        parser = IdealistaListingParser()
        collection = parser.parse(html, operation="sale")
        assert len(collection) == 0

    def test_empty_page_yields_empty_collection(self) -> None:
        parser = IdealistaListingParser()
        collection = parser.parse("<html><body></body></html>", operation="sale")
        assert len(collection) == 0
