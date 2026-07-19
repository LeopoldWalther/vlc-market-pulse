"""
Composition root for the Idealista web scraper (FEATURE-002, task 1.10).

:func:`build_orchestrator` is the single place that wires up concrete
AWS/HTTP adapters and injects them into a
:class:`~orchestrator.ScrapeOrchestrator` (Factory pattern / Dependency
Inversion at the composition edge — mirrors
``idealista_listings_collector.py``'s ``build_collector``). Callers
(:mod:`__main__`, and Phase 2's ``run_task``) never construct
:class:`~fetcher.CloudscraperFetcher`, :class:`~proxies.ProxyProvider`
implementations, or :class:`~repository.ListingRepository`
implementations themselves.
"""

from __future__ import annotations

from typing import Optional

from common.secrets_provider import SecretsManagerProvider

from .config import ScraperConfig
from .errors import ScraperError
from .fetcher import CloudscraperFetcher
from .orchestrator import ScrapeOrchestrator
from .parser import IdealistaListingParser
from .proxies import ProxyProvider, ProxyProviderFactory
from .repository import (
    DEFAULT_OUTPUT_DIR,
    ListingRepository,
    LocalListingRepository,
    S3ListingRepository,
)


def _build_repository(
    config: ScraperConfig, output_dir: Optional[str]
) -> ListingRepository:
    """
    Select Local vs. S3 storage.

    Args:
        config: The parsed scraper configuration.
        output_dir: When given, always selects
            :class:`~repository.LocalListingRepository` (the local
            CLI/notebook path never needs AWS credentials).

    Returns:
        A ready-to-use :class:`~repository.ListingRepository`.

    Raises:
        ScraperError: If no *output_dir* is given and
            ``config.s3_bucket`` is unset (the production path has no
            storage target).
    """
    if output_dir is not None:
        return LocalListingRepository(output_dir=output_dir)

    if not config.s3_bucket:
        raise ScraperError(
            "S3_BUCKET is required when building the production graph "
            "(no --output-dir was given)"
        )
    return S3ListingRepository(bucket=config.s3_bucket, prefix=config.s3_prefix)


def _build_proxy_provider(config: ScraperConfig) -> ProxyProvider:
    """
    Select and provision the configured proxy provider.

    Args:
        config: The parsed scraper configuration.

    Returns:
        A ready-to-use :class:`~proxies.ProxyProvider`.

    Raises:
        ScraperError: If a paid provider is configured but
            ``config.proxy_secret_name`` is unset.
    """
    if config.proxy_provider.lower() == "none":
        return ProxyProviderFactory.create("none")

    if not config.proxy_secret_name:
        raise ScraperError(
            f"PROXY_SECRET_NAME is required for proxy provider "
            f"{config.proxy_provider!r}"
        )

    # Secrets Manager stays inside this composition root — never in
    # proxies.py itself (Dependency Inversion).
    credentials = SecretsManagerProvider().get_secret(config.proxy_secret_name)
    return ProxyProviderFactory.create(config.proxy_provider, **credentials)


def build_orchestrator(
    config: ScraperConfig, output_dir: Optional[str] = None
) -> ScrapeOrchestrator:
    """
    Build a fully-wired :class:`~orchestrator.ScrapeOrchestrator`.

    Args:
        config: The parsed scraper configuration (see
            :meth:`~config.ScraperConfig.from_env`).
        output_dir: When given, wires a
            :class:`~repository.LocalListingRepository` at this path
            and a :class:`~proxies.NullProxyProvider` is used whenever
            ``config.proxy_provider == "none"`` (the default) — the
            local, no-AWS-creds path used by the CLI and the notebook.
            When omitted, the production graph is built (requires
            ``config.s3_bucket``).

    Returns:
        A ready-to-run :class:`~orchestrator.ScrapeOrchestrator`.

    Raises:
        ScraperError: On missing required configuration for the
            selected path (see :func:`_build_repository` and
            :func:`_build_proxy_provider`).
    """
    repository = _build_repository(config, output_dir)
    proxy_provider = _build_proxy_provider(config)
    fetcher = CloudscraperFetcher(proxy_provider=proxy_provider)
    parser = IdealistaListingParser()

    return ScrapeOrchestrator(
        fetcher=fetcher,
        parser=parser,
        repository=repository,
        proxy_provider=proxy_provider,
    )


__all__ = ["build_orchestrator", "DEFAULT_OUTPUT_DIR"]
