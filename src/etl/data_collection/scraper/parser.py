"""
Idealista search-results parser (FEATURE-002, task 1.7).

:data:`DOM_SELECTORS` is a module-level map from logical field name to
CSS selector: selector changes (Idealista redesigns its markup fairly
often) are data changes, never code changes (Open/Closed Principle).
:class:`IdealistaListingParser` walks the parsed DOM, extracting each
field defensively — every ``.select_one(...)``/attribute access is
guarded so a missing/renamed element yields ``None``, never
``KeyError``/``AttributeError`` — and builds :class:`~domain.Listing`
objects via :mod:`domain` (task 1.2).
"""

from __future__ import annotations

import re
from typing import List, Optional

from bs4 import BeautifulSoup
from bs4.element import Tag

from .domain import Listing, ListingCollection

#: Logical field name -> CSS selector, scoped to one ``article.item``.
#: Kept as data (not inlined in the parsing logic) so a markup change on
#: Idealista's side is a one-line fix here.
DOM_SELECTORS = {
    "article": "main.listing-items article.item",
    "link": "div.item-info-container a.item-link",
    "price": "span.item-price",
    "detail": "div.item-detail-char span.item-detail",
    "description": "div.item-description p",
}

_ROOMS_RE = re.compile(r"(\d+)\s*room", re.IGNORECASE)
_SIZE_RE = re.compile(r"([\d.,]+)\s*m", re.IGNORECASE)
_FLOOR_RE = re.compile(r"(ground|\d+\w*)\s*floor", re.IGNORECASE)
_PROPERTY_CODE_RE = re.compile(r"/inmueble/(\d+)/")


def _text_or_none(tag: Optional[Tag]) -> Optional[str]:
    """Return stripped text of *tag*, or ``None`` when *tag* is absent."""
    if tag is None:
        return None
    text = tag.get_text(strip=True)
    return text or None


def _extract_property_code(article: Tag, link_href: Optional[str]) -> Optional[str]:
    """
    Extract the property code from ``data-element-id`` or the listing URL.

    Args:
        article: The ``article.item`` tag.
        link_href: The href of the primary listing link, if any.

    Returns:
        The numeric property code as a string, or ``None``.
    """
    element_id = article.get("data-element-id")
    if element_id:
        return str(element_id)
    if link_href:
        match = _PROPERTY_CODE_RE.search(link_href)
        if match:
            return match.group(1)
    return None


def _extract_detail_fields(
    details: List[Tag],
) -> tuple[Optional[int], Optional[float], Optional[str]]:
    """
    Parse rooms/size/floor out of the ``.item-detail`` span texts.

    Idealista renders these as a small, order-independent set of spans
    (e.g. ``"3 rooms"``, ``"132 m²"``, ``"2nd floor"``); each is matched
    by its own pattern rather than relying on position, so a missing
    detail (e.g. no floor for a ground-floor studio) doesn't shift the
    others.

    Args:
        details: The list of ``.item-detail`` tags for one listing.

    Returns:
        ``(rooms, size, floor)``, each ``None`` when not present.
    """
    rooms: Optional[int] = None
    size: Optional[float] = None
    floor: Optional[str] = None

    for detail in details:
        text = _text_or_none(detail)
        if text is None:
            continue

        rooms_match = _ROOMS_RE.search(text)
        if rooms_match and rooms is None:
            rooms = int(rooms_match.group(1))
            continue

        size_match = _SIZE_RE.search(text)
        if size_match and size is None:
            size = float(size_match.group(1).replace(",", "."))
            continue

        floor_match = _FLOOR_RE.search(text)
        if floor_match and floor is None:
            floor = floor_match.group(1)

    return rooms, size, floor


class IdealistaListingParser:
    """
    Parses one Idealista search-results HTML page into a :class:`ListingCollection`.

    Uses ``BeautifulSoup`` with the ``lxml`` parser (fast, tolerant of
    the slightly malformed markup real search-result pages sometimes
    contain).
    """

    def parse(self, html: str, operation: str) -> ListingCollection:
        """
        Parse *html* into a :class:`ListingCollection`.

        Args:
            html: The raw page HTML (as returned by
                :meth:`~fetcher.PageFetcher.fetch`).
            operation: ``"sale"`` or ``"rent"`` — stamped onto every
                :class:`Listing` produced (the page itself doesn't
                repeat the operation per card).

        Returns:
            A :class:`ListingCollection` with one :class:`Listing` per
            successfully-parsed ``article.item`` card. Cards missing a
            property code are skipped (never raise).
        """
        soup = BeautifulSoup(html, "lxml")
        collection = ListingCollection()

        for article in soup.select(DOM_SELECTORS["article"]):
            listing = self._parse_article(article, operation)
            if listing is not None:
                collection.add(listing)

        return collection

    def _parse_article(self, article: Tag, operation: str) -> Optional[Listing]:
        """Parse one ``article.item`` card; return ``None`` if it lacks a code."""
        link = article.select_one(DOM_SELECTORS["link"])
        link_href = link.get("href") if link is not None else None
        link_href_str = str(link_href) if link_href else None

        property_code = _extract_property_code(article, link_href_str)
        if not property_code:
            return None

        address = _text_or_none(link)
        price = _text_or_none(article.select_one(DOM_SELECTORS["price"]))
        details = article.select(DOM_SELECTORS["detail"])
        rooms, size, floor = _extract_detail_fields(details)

        url = (
            f"https://www.idealista.com{link_href_str}"
            if link_href_str and link_href_str.startswith("/")
            else link_href_str
        )

        return Listing(
            property_code=property_code,
            price=price,
            size=size,
            rooms=rooms,
            bathrooms=None,
            address=address,
            url=url,
            operation=operation,
            floor=floor,
        )
