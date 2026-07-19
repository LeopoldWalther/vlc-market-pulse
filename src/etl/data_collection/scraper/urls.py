"""
URL construction for the Idealista web scraper (FEATURE-002, task 1.3).

:class:`OperationStrategy` (Strategy pattern) encapsulates the two
interchangeable Idealista search variants — sale vs. rent — behind a
narrow ABC so :class:`IdealistaUrlBuilder` (Builder pattern) and, later,
:class:`~orchestrator.ScrapeOrchestrator`, never branch on operation:
adding a new operation means adding a new :class:`OperationStrategy`
subclass, not editing existing code (Open/Closed Principle).

No size/elevator/preservation filter segments are added deliberately —
this scraper targets the *full* Valencia inventory (unlike the filtered
API collector), so :class:`IdealistaUrlBuilder` only ever varies the
operation segment and the page number.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

#: Idealista's public (non-API) search UI, English locale, Valencia city.
_BASE_URL = "https://www.idealista.com/en"
_LOCATION_SEGMENT = "valencia-valencia"


class OperationStrategy(ABC):
    """
    Strategy: the URL segment and label for one Idealista operation.

    Concrete subclasses (:class:`SaleStrategy`, :class:`RentStrategy`)
    are fully interchangeable (Liskov Substitution): any code depending
    on this ABC works unchanged regardless of which variant is injected.
    """

    @property
    @abstractmethod
    def segment(self) -> str:
        """The URL path segment for this operation (e.g. ``"venta-viviendas"``)."""

    @property
    @abstractmethod
    def operation_label(self) -> str:
        """The short label used elsewhere in the domain (``"sale"``/``"rent"``)."""


class SaleStrategy(OperationStrategy):
    """Strategy variant for Idealista's sale ("venta") listings."""

    @property
    def segment(self) -> str:
        """Path segment for sale search results."""
        return "venta-viviendas"

    @property
    def operation_label(self) -> str:
        """Domain operation label for sale listings."""
        return "sale"


class RentStrategy(OperationStrategy):
    """Strategy variant for Idealista's rental ("alquiler") listings."""

    @property
    def segment(self) -> str:
        """Path segment for rent search results."""
        return "alquiler-viviendas"

    @property
    def operation_label(self) -> str:
        """Domain operation label for rent listings."""
        return "rent"


class IdealistaUrlBuilder:
    """
    Builder: assembles a paginated Idealista search-results URL.

    Depends only on the :class:`OperationStrategy` abstraction
    (Dependency Inversion) — swapping sale/rent (or adding a future
    operation) requires no change here.
    """

    def __init__(self, strategy: OperationStrategy) -> None:
        """
        Args:
            strategy: The operation strategy (sale/rent) to build URLs for.
        """
        self._strategy = strategy

    def build(self, page: int) -> str:
        """
        Build the search-results URL for *page*.

        Args:
            page: 1-indexed page number.

        Returns:
            e.g. ``"https://www.idealista.com/en/venta-viviendas/
            valencia-valencia/?pagina=2"``.

        Raises:
            ValueError: If *page* is less than 1.
        """
        if page < 1:
            raise ValueError(f"page must be >= 1, got {page}")
        return (
            f"{_BASE_URL}/{self._strategy.segment}/{_LOCATION_SEGMENT}/?pagina={page}"
        )
