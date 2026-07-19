"""
Unit tests for proxy providers and the factory (FEATURE-002, task 1.4).
"""

from __future__ import annotations

import pytest

from data_collection.scraper.errors import ProxyError
from data_collection.scraper.proxies import (
    NullProxyProvider,
    ProxyProvider,
    ProxyProviderFactory,
    ProxyRackProvider,
    RayobyteProxyProvider,
)

CREDENTIALS = {
    "host": "gate.proxy.example.com",
    "port": 9000,
    "username": "user123",
    "password": "secret456",
}


class TestNullProxyProvider:
    def test_get_proxy_returns_none(self) -> None:
        assert NullProxyProvider().get_proxy() is None

    def test_rotate_is_a_noop(self) -> None:
        provider = NullProxyProvider()
        provider.rotate()  # should not raise
        assert provider.get_proxy() is None

    def test_requires_no_credentials(self) -> None:
        # Constructing with zero args must succeed.
        NullProxyProvider()


class TestCredentialBackedProviders:
    @pytest.mark.parametrize("provider_cls", [RayobyteProxyProvider, ProxyRackProvider])
    def test_builds_requests_style_proxy_dict(self, provider_cls: type) -> None:
        provider = provider_cls(**CREDENTIALS)
        proxy = provider.get_proxy()
        assert proxy is not None
        assert proxy["http"].startswith("http://user123:secret456@")
        assert "gate.proxy.example.com:9000" in proxy["http"]
        assert proxy["https"] == proxy["http"]

    @pytest.mark.parametrize("provider_cls", [RayobyteProxyProvider, ProxyRackProvider])
    def test_rotate_increments_rotation_count(self, provider_cls: type) -> None:
        provider = provider_cls(**CREDENTIALS)
        assert provider.rotation_count == 0
        provider.rotate()
        provider.rotate()
        assert provider.rotation_count == 2

    @pytest.mark.parametrize("provider_cls", [RayobyteProxyProvider, ProxyRackProvider])
    def test_rejects_missing_credentials(self, provider_cls: type) -> None:
        with pytest.raises(ProxyError):
            provider_cls(host="", port=9000, username="u", password="p")


class TestProxyProviderFactory:
    def test_creates_null_provider(self) -> None:
        provider = ProxyProviderFactory.create("none")
        assert isinstance(provider, NullProxyProvider)

    def test_creates_rayobyte_provider(self) -> None:
        provider = ProxyProviderFactory.create("rayobyte", **CREDENTIALS)
        assert isinstance(provider, RayobyteProxyProvider)

    def test_creates_proxyrack_provider(self) -> None:
        provider = ProxyProviderFactory.create("proxyrack", **CREDENTIALS)
        assert isinstance(provider, ProxyRackProvider)

    def test_is_case_insensitive(self) -> None:
        provider = ProxyProviderFactory.create("NONE")
        assert isinstance(provider, NullProxyProvider)

    def test_unknown_provider_raises_proxy_error(self) -> None:
        with pytest.raises(ProxyError):
            ProxyProviderFactory.create("unknown-vendor")

    def test_all_variants_satisfy_common_interface(self) -> None:
        for provider in (
            ProxyProviderFactory.create("none"),
            ProxyProviderFactory.create("rayobyte", **CREDENTIALS),
            ProxyProviderFactory.create("proxyrack", **CREDENTIALS),
        ):
            assert isinstance(provider, ProxyProvider)
