"""
Unit tests for the scraper error hierarchy (FEATURE-002, task 1.1).

RED: asserts that FetchError/ParseError/ProxyError all exist and inherit
from a single ScraperError base, so calling code can catch broadly
(``except ScraperError``) or narrowly, per failure mode.
"""

from __future__ import annotations

from data_collection.scraper.errors import (
    FetchError,
    ParseError,
    ProxyError,
    ScraperError,
)


class TestScraperErrorHierarchy:
    """Behavioural contract of the scraper's custom exception hierarchy."""

    def test_scraper_error_is_an_exception(self) -> None:
        """ScraperError is a normal Exception subclass."""
        assert issubclass(ScraperError, Exception)

    def test_fetch_error_inherits_scraper_error(self) -> None:
        """FetchError (transport/HTTP failures) inherits ScraperError."""
        assert issubclass(FetchError, ScraperError)

    def test_parse_error_inherits_scraper_error(self) -> None:
        """ParseError (DOM/parsing failures) inherits ScraperError."""
        assert issubclass(ParseError, ScraperError)

    def test_proxy_error_inherits_scraper_error(self) -> None:
        """ProxyError (proxy provisioning/rotation failures) inherits ScraperError."""
        assert issubclass(ProxyError, ScraperError)

    def test_errors_carry_a_message(self) -> None:
        """Each subclass behaves like a normal exception with a message."""
        assert str(FetchError("boom")) == "boom"
        assert str(ParseError("boom")) == "boom"
        assert str(ProxyError("boom")) == "boom"

    def test_can_catch_all_subclasses_via_base(self) -> None:
        """A broad ``except ScraperError`` catches every subclass."""
        for exc_cls in (FetchError, ParseError, ProxyError):
            try:
                raise exc_cls("boom")
            except ScraperError as caught:
                assert isinstance(caught, exc_cls)
            else:
                raise AssertionError(f"{exc_cls} did not raise as ScraperError")
