"""
Rotating proxy providers for the Idealista web scraper (FEATURE-002, task 1.4).

:class:`ProxyProvider` is a narrow Adapter interface: two operations,
``get_proxy()`` (the current requests-style proxy dict) and ``rotate()``
(advance to a fresh exit IP). Concrete adapters
(:class:`RayobyteProxyProvider`, :class:`ProxyRackProvider`) build that
dict from constructor-injected credentials only — this module never
reads environment variables or talks to AWS Secrets Manager directly
(Dependency Inversion: the composition root in :mod:`factory`, task
1.10, is responsible for sourcing credentials and injecting them here).

:class:`NullProxyProvider` always returns ``None`` and no-ops on
``rotate()``, so the entire scraper core runs locally with zero proxy
credentials (used by the CLI and the learning notebook).

:class:`ProxyProviderFactory` selects the concrete provider by name
(Open/Closed: adding a new provider is a new class + one factory
branch, never a change to callers of :class:`ProxyProvider`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional

from .errors import ProxyError


class ProxyProvider(ABC):
    """
    Adapter interface: a rotating source of HTTP(S) proxy dicts.

    Concrete providers are interchangeable (Liskov Substitution): any
    code depending on this ABC (e.g. :class:`~fetcher.PageFetcher`)
    works unchanged whether it is wired to Rayobyte, ProxyRack, or no
    proxy at all.
    """

    @abstractmethod
    def get_proxy(self) -> Optional[Dict[str, str]]:
        """
        Return the current proxy as a ``requests``-style dict.

        Returns:
            ``{"http": "...", "https": "..."}``, or ``None`` when no
            proxy should be used (e.g. :class:`NullProxyProvider`).
        """

    @abstractmethod
    def rotate(self) -> None:
        """Advance to a fresh exit IP for subsequent requests."""


class _CredentialProxyProvider(ProxyProvider):
    """
    Shared behaviour for credential-backed proxy vendors.

    Both Rayobyte and ProxyRack expose a rotating-gateway endpoint where
    ``rotate()`` is a logical no-op against the HTTP call itself (the
    vendor's gateway rotates the exit IP on each new connection) but the
    scraper still tracks a rotation counter so tests and logs can assert
    that a rotation was requested between pages.
    """

    def __init__(self, host: str, port: int, username: str, password: str) -> None:
        """
        Args:
            host: Proxy gateway hostname.
            port: Proxy gateway port.
            username: Proxy account username.
            password: Proxy account password.

        Raises:
            ProxyError: If any credential is empty.
        """
        if not all([host, username, password]):
            raise ProxyError(
                "Proxy host/username/password are required and cannot be empty"
            )
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._rotations = 0

    @property
    def rotation_count(self) -> int:
        """Number of times :meth:`rotate` has been called (test/log helper)."""
        return self._rotations

    def get_proxy(self) -> Dict[str, str]:
        """Build the ``requests``-style proxy dict from injected credentials."""
        auth = f"{self._username}:{self._password}"
        proxy_url = f"http://{auth}@{self._host}:{self._port}"
        return {"http": proxy_url, "https": proxy_url}

    def rotate(self) -> None:
        """Record a rotation request (the vendor gateway rotates the exit IP)."""
        self._rotations += 1


class RayobyteProxyProvider(_CredentialProxyProvider):
    """Rotating residential proxy adapter for the Rayobyte vendor."""


class ProxyRackProvider(_CredentialProxyProvider):
    """Rotating residential proxy adapter for the ProxyRack vendor."""


class NullProxyProvider(ProxyProvider):
    """
    No-op :class:`ProxyProvider` for local development and testing.

    Requires no credentials; ``get_proxy()`` always returns ``None`` so
    ``requests``/``cloudscraper`` connect directly.
    """

    def get_proxy(self) -> Optional[Dict[str, str]]:
        """Always ``None`` — no proxy is used."""
        return None

    def rotate(self) -> None:
        """No-op: there is no active proxy to rotate."""


class ProxyProviderFactory:
    """
    Factory: build the configured :class:`ProxyProvider` by name.

    Open/Closed: callers depend only on this factory and the
    :class:`ProxyProvider` abstraction; adding a fourth vendor never
    requires touching the fetcher/orchestrator.
    """

    _PROVIDERS = {
        "rayobyte": RayobyteProxyProvider,
        "proxyrack": ProxyRackProvider,
        "none": NullProxyProvider,
    }

    @classmethod
    def create(cls, provider_name: str, **credentials: object) -> ProxyProvider:
        """
        Build the proxy provider named *provider_name*.

        Args:
            provider_name: One of ``"rayobyte"``, ``"proxyrack"``,
                ``"none"`` (case-insensitive).
            **credentials: Forwarded to the provider's constructor
                (``host``, ``port``, ``username``, ``password``); unused
                and may be omitted for ``"none"``.

        Returns:
            A ready-to-use :class:`ProxyProvider`.

        Raises:
            ProxyError: If *provider_name* is not recognised.
        """
        key = provider_name.lower()
        try:
            provider_cls = cls._PROVIDERS[key]
        except KeyError as exc:
            raise ProxyError(
                f"Unknown proxy provider {provider_name!r}; "
                f"expected one of {sorted(cls._PROVIDERS)}"
            ) from exc

        if provider_cls is NullProxyProvider:
            return NullProxyProvider()
        return provider_cls(**credentials)  # type: ignore[arg-type]
