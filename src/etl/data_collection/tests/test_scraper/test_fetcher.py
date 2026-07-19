"""
Unit tests for the page fetcher's retry/backoff Template Method (FEATURE-002, task 1.5).

Uses a stub ``PageFetcher`` subclass to test the retry/backoff/rotation
skeleton in isolation from any real HTTP library, plus a
``cloudscraper``-mocked test for :class:`CloudscraperFetcher` itself.
All ``time.sleep`` calls are injected/mocked so tests run instantly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from data_collection.scraper.errors import FetchError
from data_collection.scraper.fetcher import CloudscraperFetcher, PageFetcher
from data_collection.scraper.proxies import NullProxyProvider, ProxyProvider


@dataclass
class _StubResponse:
    status_code: int
    text: str = ""


class _FakeProxyProvider(ProxyProvider):
    """Records rotate() calls; always returns a fixed proxy dict."""

    def __init__(self) -> None:
        self.rotations = 0

    def get_proxy(self) -> Optional[Dict[str, str]]:
        return {"http": "http://proxy", "https": "http://proxy"}

    def rotate(self) -> None:
        self.rotations += 1


class _ScriptedFetcher(PageFetcher):
    """PageFetcher whose _transport() replays a scripted response sequence."""

    def __init__(self, responses: List[_StubResponse], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._responses = list(responses)
        self.calls: List[Optional[Dict[str, str]]] = []

    def _transport(self, url: str, proxy: Optional[Dict[str, str]]) -> _StubResponse:
        self.calls.append(proxy)
        if not self._responses:
            raise AssertionError("no more scripted responses")
        return self._responses.pop(0)


def _sleep_recorder() -> tuple:
    """Return (sleep_fn, calls_list) for asserting backoff delays."""
    calls: List[float] = []

    def _sleep(seconds: float) -> None:
        calls.append(seconds)

    return _sleep, calls


class TestPageFetcherRetryBackoff:
    def test_returns_text_on_first_success(self) -> None:
        sleep_fn, sleep_calls = _sleep_recorder()
        proxy_provider = _FakeProxyProvider()
        fetcher = _ScriptedFetcher(
            [_StubResponse(200, "<html>ok</html>")],
            proxy_provider=proxy_provider,
            sleep_fn=sleep_fn,
        )

        result = fetcher.fetch("https://example.com")

        assert result == "<html>ok</html>"
        assert sleep_calls == []
        assert proxy_provider.rotations == 0

    @pytest.mark.parametrize("status", [429, 403, 503])
    def test_retries_and_succeeds_after_retryable_status(self, status: int) -> None:
        sleep_fn, sleep_calls = _sleep_recorder()
        proxy_provider = _FakeProxyProvider()
        fetcher = _ScriptedFetcher(
            [_StubResponse(status), _StubResponse(200, "<html>ok</html>")],
            proxy_provider=proxy_provider,
            sleep_fn=sleep_fn,
            base_delay=1.0,
        )

        result = fetcher.fetch("https://example.com")

        assert result == "<html>ok</html>"
        assert sleep_calls == [1.0]
        assert proxy_provider.rotations == 1

    def test_exponential_backoff_delays(self) -> None:
        sleep_fn, sleep_calls = _sleep_recorder()
        proxy_provider = _FakeProxyProvider()
        fetcher = _ScriptedFetcher(
            [
                _StubResponse(429),
                _StubResponse(429),
                _StubResponse(429),
                _StubResponse(200, "ok"),
            ],
            proxy_provider=proxy_provider,
            sleep_fn=sleep_fn,
            base_delay=1.0,
        )

        fetcher.fetch("https://example.com")

        assert sleep_calls == [1.0, 2.0, 4.0]
        assert proxy_provider.rotations == 3

    def test_raises_fetch_error_after_exhausting_retries(self) -> None:
        sleep_fn, _ = _sleep_recorder()
        proxy_provider = _FakeProxyProvider()
        fetcher = _ScriptedFetcher(
            [_StubResponse(429)] * 5,
            proxy_provider=proxy_provider,
            sleep_fn=sleep_fn,
            base_delay=0.01,
            max_retries=4,
        )

        with pytest.raises(FetchError):
            fetcher.fetch("https://example.com")

        assert proxy_provider.rotations == 4

    def test_non_retryable_status_raises_immediately(self) -> None:
        sleep_fn, sleep_calls = _sleep_recorder()
        proxy_provider = _FakeProxyProvider()
        fetcher = _ScriptedFetcher(
            [_StubResponse(500)],
            proxy_provider=proxy_provider,
            sleep_fn=sleep_fn,
        )

        with pytest.raises(FetchError):
            fetcher.fetch("https://example.com")

        assert sleep_calls == []

    def test_transport_exception_is_retried(self) -> None:
        sleep_fn, sleep_calls = _sleep_recorder()
        proxy_provider = _FakeProxyProvider()

        class _FlakyFetcher(PageFetcher):
            def __init__(self, **kwargs: object) -> None:
                super().__init__(**kwargs)  # type: ignore[arg-type]
                self._attempt = 0

            def _transport(
                self, url: str, proxy: Optional[Dict[str, str]]
            ) -> _StubResponse:
                self._attempt += 1
                if self._attempt == 1:
                    raise ConnectionError("boom")
                return _StubResponse(200, "recovered")

        fetcher = _FlakyFetcher(proxy_provider=proxy_provider, sleep_fn=sleep_fn)
        result = fetcher.fetch("https://example.com")

        assert result == "recovered"
        assert sleep_calls == [1.0]

    def test_proxy_from_provider_is_passed_to_transport(self) -> None:
        sleep_fn, _ = _sleep_recorder()
        proxy_provider = _FakeProxyProvider()
        fetcher = _ScriptedFetcher(
            [_StubResponse(200, "ok")],
            proxy_provider=proxy_provider,
            sleep_fn=sleep_fn,
        )

        fetcher.fetch("https://example.com")

        assert fetcher.calls == [{"http": "http://proxy", "https": "http://proxy"}]


class TestCloudscraperFetcher:
    """CloudscraperFetcher's transport, with cloudscraper fully mocked."""

    def test_transport_uses_cloudscraper_and_returns_response(self) -> None:
        fake_response = _StubResponse(200, "<html>from cloudscraper</html>")
        fake_scraper = MagicMock()
        fake_scraper.get.return_value = fake_response

        sleep_fn, _ = _sleep_recorder()
        fetcher = CloudscraperFetcher(
            proxy_provider=NullProxyProvider(), sleep_fn=sleep_fn
        )

        with patch("cloudscraper.create_scraper", return_value=fake_scraper) as create:
            result = fetcher.fetch("https://www.idealista.com/en/venta-viviendas/x/")

        assert result == "<html>from cloudscraper</html>"
        create.assert_called_once()
        fake_scraper.get.assert_called_once()
        _, call_kwargs = fake_scraper.get.call_args
        assert "User-Agent" in call_kwargs["headers"]
        assert call_kwargs["proxies"] is None
