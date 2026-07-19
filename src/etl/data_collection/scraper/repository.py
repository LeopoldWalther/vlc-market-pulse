"""
Listing repositories for the Idealista web scraper (FEATURE-002, task 1.8).

:class:`ListingRepository` is a narrow Repository abstraction — one
``save`` operation — implemented by :class:`S3ListingRepository`
(boto3, production) and :class:`LocalListingRepository` (disk, local
development/notebook). Both satisfy the same interface (Dependency
Inversion / Liskov Substitution): :class:`~orchestrator.ScrapeOrchestrator`
depends only on :class:`ListingRepository` and never knows which
storage backend is behind it.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: Default S3 key prefix — a sibling namespace to the API collector's
#: ``bronze/idealista/`` (task 1.8's acceptance criteria keep the
#: scraper's output clearly attributable/separable from the API data).
DEFAULT_S3_PREFIX = "bronze/idealista-scraper/"

#: Default local output directory, mirroring the existing
#: ``data/s3/*.json`` convention used by the API collector's local runs.
DEFAULT_OUTPUT_DIR = "data/s3"


class ListingRepository(ABC):
    """
    Repository: persists one scraped page's JSON envelope.

    A single operation (Interface Segregation) — callers never list,
    delete or update through this interface, only ``save``.
    """

    @abstractmethod
    def save(
        self,
        envelope: Dict[str, Any],
        operation: str,
        page: int,
        date: Optional[str] = None,
    ) -> str:
        """
        Persist *envelope* and return the key/path it was written to.

        Args:
            envelope: The JSON-serialisable envelope (typically from
                :meth:`~domain.ListingCollection.to_envelope`).
            operation: ``"sale"`` or ``"rent"``.
            page: The 1-indexed page number.
            date: Optional ``YYYY-MM-DD`` override (defaults to today,
                UTC); mainly a test seam.

        Returns:
            The S3 key or local filesystem path written.
        """


class S3ListingRepository(ListingRepository):
    """
    boto3-backed :class:`ListingRepository` adapter for a single S3 bucket.

    Key layout: ``{prefix}{YYYY-MM-DD}/{operation}_page{N}.json``
    (Adapter pattern — wraps ``put_object`` behind the project-owned
    interface; mirrors ``common/object_store.py``'s S3 confinement).
    """

    def __init__(
        self,
        bucket: str,
        s3_client: object | None = None,
        prefix: str = DEFAULT_S3_PREFIX,
    ) -> None:
        """
        Args:
            bucket: Target S3 bucket name.
            s3_client: Optional pre-built boto3 S3 client (injected in
                tests via moto). Created lazily from boto3 when omitted.
            prefix: Key prefix for every object this repository writes.
        """
        # boto3 stays inside the adapter (Dependency Inversion).
        import boto3

        self._bucket = bucket
        self._client = s3_client if s3_client is not None else boto3.client("s3")
        self._prefix = prefix

    def save(
        self,
        envelope: Dict[str, Any],
        operation: str,
        page: int,
        date: Optional[str] = None,
    ) -> str:
        """Write *envelope* to ``s3://{bucket}/{prefix}{date}/{operation}_page{N}.json``."""
        date_str = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"{self._prefix}{date_str}/{operation}_page{page}.json"
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")

        self._client.put_object(  # type: ignore[attr-defined]
            Bucket=self._bucket, Key=key, Body=body, ContentType="application/json"
        )
        logger.info("Wrote %d bytes to s3://%s/%s", len(body), self._bucket, key)
        return key


class LocalListingRepository(ListingRepository):
    """
    Disk-backed :class:`ListingRepository` adapter for local development.

    Filename layout: ``{operation}_scraper_{YYYYMMDD_HHMMSS}_{page}.json``
    inside *output_dir* — loosely mirrors the API collector's
    ``data/s3/{operation}_{timestamp}_{page}.json`` naming (see
    ``data/s3/sale_20250413_120045_3.json``) while the ``scraper``
    infix keeps the two sources visually distinguishable on disk.
    """

    def __init__(self, output_dir: str = DEFAULT_OUTPUT_DIR) -> None:
        """
        Args:
            output_dir: Directory JSON files are written into (created
                if missing).
        """
        self._output_dir = Path(output_dir)

    def save(
        self,
        envelope: Dict[str, Any],
        operation: str,
        page: int,
        date: Optional[str] = None,
    ) -> str:
        """Write *envelope* to a timestamped JSON file under ``output_dir``."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = (
            date or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        ).replace("-", "")
        filename = f"{operation}_scraper_{timestamp}_{page}.json"
        path = self._output_dir / filename

        path.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("Wrote %s", path)
        return str(path)
