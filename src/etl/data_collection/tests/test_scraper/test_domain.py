"""
Unit tests for the scraper domain model (FEATURE-002, task 1.2).

Covers the acceptance criteria in the technical plan: camelCase
``to_dict()`` keys with nulls for missing optional fields, price/size
normalisation, property_code-based equality/dedup, and the
``ListingCollection.to_envelope()`` shape.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from data_collection.scraper.domain import Listing, ListingCollection


def _make_listing(**overrides: object) -> Listing:
    """Build a fully-populated Listing, overridable per test."""
    defaults: dict = {
        "property_code": "107517743",
        "price": 695000.0,
        "size": 132.0,
        "rooms": 3,
        "bathrooms": 2,
        "address": "calle de Ruzafa",
        "url": "https://www.idealista.com/inmueble/107517743/",
        "operation": "sale",
        "floor": "3",
        "exterior": True,
        "province": "València",
        "municipality": "València",
        "district": "L'Eixample",
        "neighborhood": "El Pla del Remei",
        "latitude": 39.4664014,
        "longitude": -0.3725843,
        "property_type": "flat",
        "thumbnail": "https://img4.idealista.com/blur/thumb.webp",
    }
    defaults.update(overrides)
    return Listing(**defaults)


class TestListingValidation:
    """Constructor validation."""

    def test_requires_property_code(self) -> None:
        with pytest.raises(ValueError):
            _make_listing(property_code="")

    def test_requires_valid_operation(self) -> None:
        with pytest.raises(ValueError):
            _make_listing(operation="lease")

    def test_accepts_sale_and_rent(self) -> None:
        assert _make_listing(operation="sale").operation == "sale"
        assert _make_listing(operation="rent").operation == "rent"


class TestListingNormalisation:
    """Price/size normalisation from raw parser strings."""

    def test_numeric_price_and_size_pass_through(self) -> None:
        listing = _make_listing(price=695000.0, size=132.0)
        assert listing.price == 695000.0
        assert listing.size == 132.0

    def test_price_string_with_thousands_separator_and_currency(self) -> None:
        listing = _make_listing(price="695.000 €")
        assert listing.price == 695000.0

    def test_size_string_with_unit(self) -> None:
        listing = _make_listing(size="132 m²")
        assert listing.size == 132.0

    def test_decimal_comma_size(self) -> None:
        listing = _make_listing(size="132,5 m²")
        assert listing.size == 132.5

    def test_rooms_and_bathrooms_from_strings(self) -> None:
        listing = _make_listing(rooms="3", bathrooms="2")
        assert listing.rooms == 3
        assert listing.bathrooms == 2

    def test_missing_numeric_fields_are_none(self) -> None:
        listing = _make_listing(price=None, size=None, rooms=None, bathrooms=None)
        assert listing.price is None
        assert listing.size is None
        assert listing.rooms is None
        assert listing.bathrooms is None


class TestListingToDict:
    """to_dict() camelCase schema."""

    def test_keys_match_api_element_list_schema(self) -> None:
        listing = _make_listing()
        expected_keys = {
            "propertyCode",
            "thumbnail",
            "floor",
            "price",
            "propertyType",
            "operation",
            "size",
            "exterior",
            "rooms",
            "bathrooms",
            "address",
            "province",
            "municipality",
            "district",
            "country",
            "neighborhood",
            "latitude",
            "longitude",
            "url",
        }
        assert set(listing.to_dict().keys()) == expected_keys

    def test_missing_optional_fields_serialise_as_none(self) -> None:
        listing = Listing(
            property_code="1",
            price=100000,
            size=50,
            rooms=1,
            bathrooms=1,
            address=None,
            url=None,
            operation="sale",
        )
        data = listing.to_dict()
        assert data["floor"] is None
        assert data["exterior"] is None
        assert data["neighborhood"] is None
        assert data["thumbnail"] is None

    def test_property_code_and_operation_round_trip(self) -> None:
        listing = _make_listing(property_code="999", operation="rent")
        data = listing.to_dict()
        assert data["propertyCode"] == "999"
        assert data["operation"] == "rent"


class TestListingEqualityAndHash:
    """Equality/hash based on property_code only (dedup contract)."""

    def test_same_property_code_are_equal(self) -> None:
        a = _make_listing(property_code="1", price=100000)
        b = _make_listing(property_code="1", price=999999)
        assert a == b

    def test_different_property_code_are_not_equal(self) -> None:
        a = _make_listing(property_code="1")
        b = _make_listing(property_code="2")
        assert a != b

    def test_hashable_and_usable_in_sets(self) -> None:
        a = _make_listing(property_code="1")
        b = _make_listing(property_code="1")
        assert {a, b} == {a}

    def test_not_equal_to_other_types(self) -> None:
        assert _make_listing() != "not-a-listing"


class TestListingCollection:
    """Aggregation, dedup, and envelope construction."""

    def test_add_and_len(self) -> None:
        collection = ListingCollection()
        collection.add(_make_listing(property_code="1"))
        collection.add(_make_listing(property_code="2"))
        assert len(collection) == 2

    def test_dedups_on_property_code(self) -> None:
        collection = ListingCollection()
        collection.add(_make_listing(property_code="1", price=100000))
        collection.add(_make_listing(property_code="1", price=200000))
        assert len(collection) == 1
        assert collection.to_list()[0].price == 200000

    def test_iterates_in_insertion_order(self) -> None:
        collection = ListingCollection()
        collection.add(_make_listing(property_code="1"))
        collection.add(_make_listing(property_code="2"))
        codes = [listing.property_code for listing in collection]
        assert codes == ["1", "2"]

    def test_seeded_from_iterable(self) -> None:
        listings = [_make_listing(property_code="1"), _make_listing(property_code="2")]
        collection = ListingCollection(listings)
        assert len(collection) == 2

    def test_to_envelope_shape(self) -> None:
        collection = ListingCollection([_make_listing(property_code="1")])
        envelope = collection.to_envelope(operation="sale", page=1, total_pages=5)

        assert envelope["operation"] == "sale"
        assert envelope["source"] == "scraper"
        assert envelope["page"] == 1
        assert envelope["totalPages"] == 5
        assert len(envelope["elementList"]) == 1
        assert envelope["elementList"][0]["propertyCode"] == "1"
        # collected_at is a real ISO-8601 timestamp.
        datetime.fromisoformat(envelope["collected_at"])

    def test_empty_collection_envelope(self) -> None:
        collection = ListingCollection()
        envelope = collection.to_envelope(operation="rent", page=1, total_pages=1)
        assert envelope["elementList"] == []
