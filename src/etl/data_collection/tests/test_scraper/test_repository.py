"""
Unit tests for the S3 and Local listing repositories (FEATURE-002, task 1.8).

Uses moto for :class:`S3ListingRepository` and ``tmp_path`` for
:class:`LocalListingRepository`, mirroring the pattern in
``common/tests/test_object_store.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Generator

import boto3
import pytest
from moto import mock_aws

from data_collection.scraper.repository import (
    LocalListingRepository,
    ListingRepository,
    S3ListingRepository,
)

BUCKET = "test-scraper-bucket"

ENVELOPE = {
    "operation": "sale",
    "source": "scraper",
    "collected_at": "2026-06-01T12:00:00+00:00",
    "page": 1,
    "totalPages": 5,
    "elementList": [{"propertyCode": "1", "price": 100000.0}],
}


@pytest.fixture()
def s3_repository() -> Generator[S3ListingRepository, None, None]:
    """Yield an S3ListingRepository backed by a moto-mocked bucket."""
    with mock_aws():
        client = boto3.client("s3", region_name="eu-central-1")
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        yield S3ListingRepository(bucket=BUCKET, s3_client=client)


class TestS3ListingRepository:
    def test_save_writes_expected_key_layout(
        self, s3_repository: S3ListingRepository
    ) -> None:
        key = s3_repository.save(ENVELOPE, operation="sale", page=1, date="2026-06-01")
        assert key == "bronze/idealista-scraper/2026-06-01/sale_page1.json"

    def test_save_writes_the_envelope_body(
        self, s3_repository: S3ListingRepository
    ) -> None:
        key = s3_repository.save(ENVELOPE, operation="sale", page=1, date="2026-06-01")

        client = boto3.client("s3", region_name="eu-central-1")
        obj = client.get_object(Bucket=BUCKET, Key=key)
        body = json.loads(obj["Body"].read())
        assert body == ENVELOPE

    def test_save_uses_rent_operation_in_filename(
        self, s3_repository: S3ListingRepository
    ) -> None:
        key = s3_repository.save(ENVELOPE, operation="rent", page=2, date="2026-06-01")
        assert key == "bronze/idealista-scraper/2026-06-01/rent_page2.json"

    def test_is_a_listing_repository(self, s3_repository: S3ListingRepository) -> None:
        assert isinstance(s3_repository, ListingRepository)


class TestLocalListingRepository:
    def test_save_writes_json_file_under_output_dir(self, tmp_path: Path) -> None:
        repository = LocalListingRepository(output_dir=str(tmp_path))
        path_str = repository.save(
            ENVELOPE, operation="sale", page=1, date="20260601_120000"
        )

        path = Path(path_str)
        assert path.exists()
        assert path.parent == tmp_path
        assert json.loads(path.read_text()) == ENVELOPE

    def test_filename_contains_operation_and_page(self, tmp_path: Path) -> None:
        repository = LocalListingRepository(output_dir=str(tmp_path))
        path_str = repository.save(
            ENVELOPE, operation="rent", page=3, date="20260601_120000"
        )
        assert Path(path_str).name == "rent_scraper_20260601_120000_3.json"

    def test_creates_output_dir_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested" / "dir"
        repository = LocalListingRepository(output_dir=str(nested))
        repository.save(ENVELOPE, operation="sale", page=1, date="20260601_120000")
        assert nested.exists()

    def test_is_a_listing_repository(self, tmp_path: Path) -> None:
        repository = LocalListingRepository(output_dir=str(tmp_path))
        assert isinstance(repository, ListingRepository)


class TestRepositoryDependencyInversion:
    def test_both_implement_the_same_interface(self, tmp_path: Path) -> None:
        local = LocalListingRepository(output_dir=str(tmp_path))
        assert isinstance(local, ListingRepository)
        # Behaviourally interchangeable: both accept the same call shape.
        result = local.save(ENVELOPE, operation="sale", page=1, date="20260601_120000")
        assert isinstance(result, str)
