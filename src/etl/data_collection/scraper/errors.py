"""
Custom exception hierarchy for the Idealista web scraper (FEATURE-002).

A single :class:`ScraperError` base lets calling code (the orchestrator,
the CLI, the Fargate ``run_task`` entry point) catch every scraper
failure with one ``except`` clause, while the three subclasses let
callers that care distinguish *why* it failed:

* :class:`FetchError` — transport/HTTP failures (bad status codes,
  timeouts, retry budget exhausted).
* :class:`ParseError` — DOM/parsing failures (unexpected page shape).
* :class:`ProxyError` — proxy provisioning/rotation failures.

Mirrors the existing ``IdealistaAPIError`` convention in
``bronze_collector.py`` (a single domain exception for clean caller
code), but modelled as a small hierarchy since the scraper has three
independently interesting failure modes.
"""

from __future__ import annotations


class ScraperError(Exception):
    """Base exception for all Idealista web-scraper failures."""


class FetchError(ScraperError):
    """Raised when a page cannot be fetched after the retry budget is exhausted."""


class ParseError(ScraperError):
    """Raised when a fetched page cannot be parsed into listings."""


class ProxyError(ScraperError):
    """Raised when a proxy cannot be provisioned, selected or rotated."""
