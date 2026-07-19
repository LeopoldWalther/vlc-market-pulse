"""
Runtime configuration for the Idealista web scraper (FEATURE-002, task 1.10).

:class:`ScraperConfig` reads and validates the scraper's environment
variables. Required-ness is deliberately conditional: the *local* CLI
path (``--output-dir`` + :class:`~proxies.NullProxyProvider`) needs no
AWS credentials at all, while the *production* path (S3 + a real proxy
vendor) does need ``S3_BUCKET`` and, for paid proxy vendors, credential
secrets. :mod:`factory` (the composition root) is what decides which
path is being built and raises when a genuinely required value for
*that* path is missing — this class only parses and holds values.

``SCRAPER_ENABLED`` is the operational kill switch restored by
REVIEW-FEATURE-002 finding H2: both the local CLI (:mod:`__main__`) and
the Fargate ``run_task`` entry point (Phase 2) check it *before*
building any fetcher/proxy graph, so a misbehaving scraper can be
disabled without a redeploy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

_TRUTHY_DISABLED_VALUES = {"false", "0", "no", "off"}


def _parse_bool(raw: str, default: bool) -> bool:
    """Parse an env-var string as a boolean, defaulting on an empty value."""
    if not raw:
        return default
    return raw.strip().lower() not in _TRUTHY_DISABLED_VALUES


@dataclass(frozen=True)
class ScraperConfig:
    """
    Immutable snapshot of the scraper's environment configuration.

    Attributes:
        s3_bucket: Target S3 bucket for :class:`~repository.S3ListingRepository`;
            ``None`` when running purely locally.
        s3_prefix: S3 key prefix (defaults to
            :data:`~repository.DEFAULT_S3_PREFIX`).
        sns_topic_arn: Optional SNS topic for failure alerts (Phase 2's
            ``run_task`` entry point; unused in Phase 1's local CLI).
        proxy_provider: ``"rayobyte"``, ``"proxyrack"`` or ``"none"``.
        proxy_secret_name: Optional Secrets Manager secret name holding
            proxy credentials (host/port/username/password); required
            only when ``proxy_provider`` is not ``"none"`` and the
            production graph is being built.
        aws_region: AWS region for any boto3 clients.
        scraper_enabled: The kill switch — ``False`` short-circuits the
            CLI/``run_task`` before any fetch is attempted.
    """

    s3_bucket: Optional[str] = None
    s3_prefix: str = "bronze/idealista-scraper/"
    sns_topic_arn: Optional[str] = None
    proxy_provider: str = "none"
    proxy_secret_name: Optional[str] = None
    aws_region: str = "eu-central-1"
    scraper_enabled: bool = True

    @classmethod
    def from_env(cls, env: Dict[str, str]) -> "ScraperConfig":
        """
        Build a :class:`ScraperConfig` from an environment mapping.

        Args:
            env: Environment mapping (normally ``os.environ``).

        Returns:
            The parsed configuration. No required-field validation
            happens here — see :func:`factory.build_orchestrator` for
            the path-dependent validation.
        """
        return cls(
            s3_bucket=env.get("S3_BUCKET") or None,
            s3_prefix=env.get("S3_PREFIX", "bronze/idealista-scraper/"),
            sns_topic_arn=env.get("SNS_TOPIC_ARN") or None,
            proxy_provider=env.get("PROXY_PROVIDER", "none"),
            proxy_secret_name=env.get("PROXY_SECRET_NAME") or None,
            aws_region=env.get("AWS_REGION", "eu-central-1"),
            scraper_enabled=_parse_bool(
                env.get("SCRAPER_ENABLED", "true"), default=True
            ),
        )
