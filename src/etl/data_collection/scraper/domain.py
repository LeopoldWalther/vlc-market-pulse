"""
Domain model for the Idealista web scraper (FEATURE-002, task 1.2).

:class:`Listing` is a validated domain object (not a bare dict): it
normalises the raw values a parser extracts from the DOM (which are
often strings, e.g. ``"695.000 €"`` or ``"132 m²"``) into numeric types,
exposes them as read-only properties (Encapsulation), and serialises via
:meth:`Listing.to_dict` to the same camelCase field names used by the
API collector's ``elementList`` schema (see
``data/s3/sale_20250413_120045_3.json``) so silver/gold stay
source-agnostic.

:class:`ListingCollection` aggregates :class:`Listing` instances,
de-duplicating on ``property_code``, and produces the JSON envelope
persisted by :mod:`repository` (task 1.8).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Union

Number = Union[int, float]

#: Matches the digits (and optional decimal separator) inside a raw
#: price/size string such as ``"695.000 €"`` or ``"132,5 m²"``.
_NUMERIC_RE = re.compile(r"[-+]?\d[\d.,]*")


def _to_float(value: Optional[Union[str, Number]]) -> Optional[float]:
    """
    Normalise a raw numeric field to ``float`` (or ``None``).

    Accepts already-numeric values (``int``/``float``) unchanged, and
    strings such as ``"695.000 €"`` or ``"132 m²"`` by stripping units
    and treating ``.`` as a thousands separator when followed by exactly
    three digits (the Spanish/European convention Idealista's markup
    uses), otherwise as a decimal point.

    Args:
        value: Raw price/size value, or ``None``.

    Returns:
        The parsed ``float``, or ``None`` when *value* is ``None`` or
        contains no digits.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    match = _NUMERIC_RE.search(value)
    if match is None:
        return None

    raw = match.group(0)
    # European thousands-separator convention: "695.000" -> 695000.0,
    # but "132,5" / "132.5" (a real decimal) must stay a fraction.
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    elif raw.count(".") == 1 and len(raw.split(".")[-1]) == 3:
        raw = raw.replace(".", "")

    try:
        return float(raw)
    except ValueError:
        return None


def _to_int(value: Optional[Union[str, Number]]) -> Optional[int]:
    """Normalise a raw integer field (rooms/bathrooms) to ``int``."""
    as_float = _to_float(value)
    return None if as_float is None else int(as_float)


class Listing:
    """
    Encapsulated, validated representation of one Idealista search result.

    Read-only properties expose intent, not raw fields (Encapsulation);
    equality/hashing are defined on ``property_code`` alone so
    :class:`ListingCollection` can dedup a listing that reappears across
    paginated requests (a common Idealista behaviour under load).
    """

    def __init__(
        self,
        property_code: str,
        price: Optional[Union[str, Number]],
        size: Optional[Union[str, Number]],
        rooms: Optional[Union[str, Number]],
        bathrooms: Optional[Union[str, Number]],
        address: Optional[str],
        url: Optional[str],
        operation: str,
        *,
        floor: Optional[str] = None,
        exterior: Optional[bool] = None,
        province: Optional[str] = None,
        municipality: Optional[str] = None,
        district: Optional[str] = None,
        neighborhood: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        property_type: Optional[str] = None,
        thumbnail: Optional[str] = None,
        country: str = "es",
    ) -> None:
        """
        Args:
            property_code: Idealista's numeric listing identifier
                (also embedded in *url*); required, must be non-empty.
            price: Raw or numeric sale/rent price; normalised to float.
            size: Raw or numeric surface area in m²; normalised to float.
            rooms: Raw or numeric room count; normalised to int.
            bathrooms: Raw or numeric bathroom count; normalised to int.
            address: Street-level address text (may be ``None``).
            url: Absolute listing URL (may be ``None``).
            operation: ``"sale"`` or ``"rent"``.
            floor: Floor label (e.g. ``"3"``, ``"bj"``); optional.
            exterior: Whether the property is exterior-facing; optional.
            province: Province name; optional.
            municipality: Municipality name; optional.
            district: District name; optional.
            neighborhood: Neighbourhood name; optional.
            latitude: Latitude in decimal degrees; optional.
            longitude: Longitude in decimal degrees; optional.
            property_type: Idealista property type (e.g. ``"flat"``).
            thumbnail: Thumbnail image URL; optional.
            country: ISO country code; defaults to ``"es"`` (Spain).

        Raises:
            ValueError: If *property_code* is empty/``None`` or
                *operation* is not ``"sale"``/``"rent"``.
        """
        if not property_code:
            raise ValueError("property_code is required and cannot be empty")
        if operation not in ("sale", "rent"):
            raise ValueError(f"operation must be 'sale' or 'rent', got {operation!r}")

        self._property_code = str(property_code)
        self._price = _to_float(price)
        self._size = _to_float(size)
        self._rooms = _to_int(rooms)
        self._bathrooms = _to_int(bathrooms)
        self._address = address
        self._url = url
        self._operation = operation
        self._floor = floor
        self._exterior = exterior
        self._province = province
        self._municipality = municipality
        self._district = district
        self._neighborhood = neighborhood
        self._latitude = float(latitude) if latitude is not None else None
        self._longitude = float(longitude) if longitude is not None else None
        self._property_type = property_type
        self._thumbnail = thumbnail
        self._country = country

    @property
    def property_code(self) -> str:
        """Idealista's numeric listing identifier."""
        return self._property_code

    @property
    def price(self) -> Optional[float]:
        """Normalised sale/rent price, or ``None`` when unavailable."""
        return self._price

    @property
    def size(self) -> Optional[float]:
        """Normalised surface area in m², or ``None`` when unavailable."""
        return self._size

    @property
    def rooms(self) -> Optional[int]:
        """Room count, or ``None`` when unavailable."""
        return self._rooms

    @property
    def bathrooms(self) -> Optional[int]:
        """Bathroom count, or ``None`` when unavailable."""
        return self._bathrooms

    @property
    def address(self) -> Optional[str]:
        """Street-level address text, or ``None`` when unavailable."""
        return self._address

    @property
    def url(self) -> Optional[str]:
        """Absolute listing URL, or ``None`` when unavailable."""
        return self._url

    @property
    def operation(self) -> str:
        """``"sale"`` or ``"rent"``."""
        return self._operation

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialise to the camelCase ``elementList`` schema used by the API collector.

        Missing optional fields serialise as ``None`` (JSON ``null``)
        rather than being omitted, so downstream consumers can rely on a
        stable key set regardless of source (API vs. scraper).

        Returns:
            A camelCase dict matching the fields seen in
            ``data/s3/sale_20250413_120045_3.json``'s ``elementList``.
        """
        return {
            "propertyCode": self._property_code,
            "thumbnail": self._thumbnail,
            "floor": self._floor,
            "price": self._price,
            "propertyType": self._property_type,
            "operation": self._operation,
            "size": self._size,
            "exterior": self._exterior,
            "rooms": self._rooms,
            "bathrooms": self._bathrooms,
            "address": self._address,
            "province": self._province,
            "municipality": self._municipality,
            "district": self._district,
            "country": self._country,
            "neighborhood": self._neighborhood,
            "latitude": self._latitude,
            "longitude": self._longitude,
            "url": self._url,
        }

    def __eq__(self, other: object) -> bool:
        """Two listings are equal when their ``property_code`` matches."""
        if not isinstance(other, Listing):
            return NotImplemented
        return self._property_code == other._property_code

    def __hash__(self) -> int:
        """Hash on ``property_code`` so listings dedup in sets/dicts."""
        return hash(self._property_code)

    def __repr__(self) -> str:
        """Debug-friendly representation."""
        return (
            f"Listing(property_code={self._property_code!r}, "
            f"operation={self._operation!r}, price={self._price!r})"
        )


class ListingCollection:
    """
    Aggregates :class:`Listing` objects for one scraped page (or run).

    Repository pattern collaborator: callers ``add()`` listings as they
    are parsed and later call :meth:`to_envelope` to obtain the
    persistence-ready JSON document. De-duplication on ``property_code``
    (via :class:`Listing`'s ``__eq__``/``__hash__``) means adding the
    same listing twice is a no-op.
    """

    def __init__(self, listings: Optional[Iterable[Listing]] = None) -> None:
        """
        Args:
            listings: Optional initial listings to seed the collection
                with (deduplicated, order-preserving).
        """
        self._by_code: Dict[str, Listing] = {}
        for listing in listings or []:
            self.add(listing)

    def add(self, listing: Listing) -> None:
        """
        Add *listing*, replacing any earlier entry with the same code.

        Args:
            listing: The :class:`Listing` to add.
        """
        self._by_code[listing.property_code] = listing

    def __len__(self) -> int:
        """Number of unique listings currently held."""
        return len(self._by_code)

    def __iter__(self) -> Iterator[Listing]:
        """Iterate listings in insertion order."""
        return iter(self._by_code.values())

    def to_list(self) -> List[Listing]:
        """Return the held listings as a plain list (insertion order)."""
        return list(self._by_code.values())

    def to_envelope(
        self, operation: str, page: int, total_pages: int
    ) -> Dict[str, Any]:
        """
        Build the JSON envelope persisted by a :class:`~repository.ListingRepository`.

        Args:
            operation: ``"sale"`` or ``"rent"``.
            page: The 1-indexed page number this collection represents.
            total_pages: Best-effort total page count (may equal *page*
                when the orchestrator cannot know it in advance).

        Returns:
            ``{"operation", "source", "collected_at", "page",
            "totalPages", "elementList"}``.
        """
        return {
            "operation": operation,
            "source": "scraper",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "page": page,
            "totalPages": total_pages,
            "elementList": [listing.to_dict() for listing in self],
        }
