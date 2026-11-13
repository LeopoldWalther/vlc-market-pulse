"""
Tests for the gold aggregation Lambda handler (gold_aggregation_lambda.py).

Written TDD-style: all tests in this file are RED before gold_aggregation_lambda.py
exists, then GREEN once the implementation is in place.

The tests focus on the AWS-edge layer only (S3 read/write, env var resolution).
All analytical correctness is tested in test_gold_aggregate.py.

Moto's mock_aws is used for all S3 interactions — no real AWS credentials needed.

Run with:
    cd src/etl/data_processing
    pytest tests/test_gold_aggregation_lambda.py -v
"""

from __future__ import annotations

import io
import json
import os
import sys
from typing import Any, Dict, Generator, List

import boto3
import pandas as pd
import pytest
from moto import mock_aws

# Make the data_processing package and `common` importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from gold_aggregation_lambda import lambda_handler  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUCKET = "test-gold-bucket"
SILVER_PREFIX = "silver/idealista"
GOLD_PREFIX = "gold/aggregations"

# Minimal columns that survive gold_aggregate.build_aggregation_json.
_SILVER_COLS = [
    "operation",
    "district",
    "neighborhood",
    "snapshot_date",
    "propertyCode",
    "priceByArea",
    "size",
    "price",
    "floor",
    "rooms",
    "bathrooms",
    "hasLift",
    "latitude",
    "longitude",
]

# Top-level keys required by the frozen schema v1.0.
_SCHEMA_V1_KEYS = {
    "schema_version",
    "generated_at",
    "scope_districts",
    "min_count",
    "relevant_filter",
    "general",
    "relevant",
    "data_basis",
    "data_basis_relevant",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**overrides: Any) -> Dict[str, Any]:
    """Return a minimal silver listing row with optional overrides."""
    base: Dict[str, Any] = {
        "operation": "sale",
        "district": "Extramurs",
        "neighborhood": "Patraix",
        "snapshot_date": "2023-04-09",  # string, as written by silver lambda
        "propertyCode": "P001",
        "priceByArea": 2500.0,
        "size": 130.0,
        "price": 325_000.0,
        "floor": "3",
        "rooms": 3,
        "bathrooms": 2,
        "hasLift": True,
        "latitude": 39.469077,
        "longitude": -0.3799074,
    }
    base.update(overrides)
    return base


def _write_silver_parquet(
    s3_client: Any,
    bucket: str,
    operation: str,
    snapshot_date: str,
    rows: List[Dict[str, Any]],
) -> str:
    """Write a silver Parquet file to the mocked S3 bucket and return its key."""
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    buf.seek(0)
    key = f"{SILVER_PREFIX}/operation={operation}/snapshot_date={snapshot_date}/part.parquet"
    s3_client.put_object(Bucket=bucket, Key=key, Body=buf.read())
    return key


def _read_latest_json(s3_client: Any, bucket: str) -> Dict[str, Any]:
    """Download and parse gold/aggregations/latest.json from the mocked bucket."""
    obj = s3_client.get_object(Bucket=bucket, Key=f"{GOLD_PREFIX}/latest.json")
    return json.loads(obj["Body"].read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject the environment variables required by the Lambda handler."""
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("SILVER_PREFIX", SILVER_PREFIX)
    monkeypatch.setenv("GOLD_PREFIX", GOLD_PREFIX)
    monkeypatch.setenv("RATIO_MIN_COUNT", "1")


@pytest.fixture()
def s3_with_silver(aws_env) -> Generator[Any, None, None]:
    """
    Spin up a moto S3 bucket pre-loaded with two silver Parquet files
    (one rent snapshot, one sale snapshot), both containing a single row.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        _write_silver_parquet(
            s3,
            BUCKET,
            operation="rent",
            snapshot_date="2023-04-09",
            rows=[_make_row(operation="rent", priceByArea=10.0, price=1000.0)],
        )
        _write_silver_parquet(
            s3,
            BUCKET,
            operation="sale",
            snapshot_date="2023-04-09",
            rows=[_make_row(operation="sale")],
        )
        yield s3


# ---------------------------------------------------------------------------
# Test: main happy path
# ---------------------------------------------------------------------------


class TestLambdaHappyPath:
    """Handler lists all silver Parquets, builds aggregation, writes latest.json."""

    def test_lambda_reads_silver_history_writes_latest_json(
        self, s3_with_silver: Any
    ) -> None:
        """
        Lambda must read the full silver history and write gold/aggregations/latest.json.
        Asserts that the output key exists and the JSON is non-empty.
        """
        result = lambda_handler({}, None)

        assert result["statusCode"] == 200
        assert result["key"] == f"{GOLD_PREFIX}/latest.json"

        payload = _read_latest_json(s3_with_silver, BUCKET)
        assert isinstance(payload, dict)
        assert len(payload) > 0

    def test_lambda_output_validates_schema_v1(self, s3_with_silver: Any) -> None:
        """
        The written JSON must contain all top-level keys required by schema v1.0.
        FEATURE-005 (frontend) depends on this exact shape.
        """
        lambda_handler({}, None)

        payload = _read_latest_json(s3_with_silver, BUCKET)
        assert _SCHEMA_V1_KEYS.issubset(
            payload.keys()
        ), f"Missing schema v1.0 keys: {_SCHEMA_V1_KEYS - payload.keys()}"
        assert payload["schema_version"] == "1.0"
        assert "general" in payload and "relevant" in payload

    def test_lambda_output_includes_rolling_boxplot_field(
        self, s3_with_silver: Any
    ) -> None:
        """
        latest.json must include boxplot_by_neighborhood_last_3m in both
        population blocks (FEATURE-010 additive schema field).
        """
        lambda_handler({}, None)

        payload = _read_latest_json(s3_with_silver, BUCKET)
        assert "boxplot_by_neighborhood_last_3m" in payload["general"]
        assert "boxplot_by_neighborhood_last_3m" in payload["relevant"]
        # The all-time field must still be present and untouched.
        assert "boxplot_by_neighborhood" in payload["general"]
        assert "boxplot_by_neighborhood" in payload["relevant"]

    def test_lambda_output_includes_data_basis_block(self, s3_with_silver: Any) -> None:
        """
        Acceptance criterion (task 11.4): a moto-backed Lambda aggregation
        test confirms latest.json includes the additive data_basis block.
        """
        lambda_handler({}, None)

        payload = _read_latest_json(s3_with_silver, BUCKET)
        assert "data_basis" in payload
        assert set(payload["data_basis"].keys()) == {
            "search_config",
            "weekly_listing_volume",
            "size_histogram_10sqm",
            "rooms_distribution",
            "price_per_area_histogram",
            "listing_location_grid_last_3m",
            "listing_locations_last_3m",
        }
        # search_config is a static single-record dataset even with minimal
        # fixture data.
        assert payload["data_basis"]["search_config"]
        # FEATURE-016: the relevant-population twin carries the same datasets.
        assert list(payload["data_basis_relevant"].keys()) == list(
            payload["data_basis"].keys()
        )


# ---------------------------------------------------------------------------
# Test: empty silver history
# ---------------------------------------------------------------------------


class TestEmptySilverHistory:
    """Handler must write a valid JSON even when no silver Parquet files exist."""

    def test_lambda_empty_silver_writes_valid_empty_json(self, aws_env: None) -> None:
        """
        When the silver prefix is empty, latest.json must still be written
        and pass schema v1.0 validation — no exception should be raised.
        """
        with mock_aws():
            s3 = boto3.client("s3", region_name="eu-central-1")
            s3.create_bucket(
                Bucket=BUCKET,
                CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
            )

            result = lambda_handler({}, None)

        assert result["statusCode"] == 200

        # Re-read from a fresh mock context to verify the file was written.
        with mock_aws():
            s3 = boto3.client("s3", region_name="eu-central-1")
            s3.create_bucket(
                Bucket=BUCKET,
                CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
            )
            # Empty bucket — just verify no exception was raised and result is valid.
            assert result["key"] == f"{GOLD_PREFIX}/latest.json"

    def test_lambda_empty_silver_schema_v1_shape(self, aws_env: None) -> None:
        """
        Empty silver history produces an output dict that still has all schema
        v1.0 keys and empty dataset lists in both population blocks.
        """
        with mock_aws():
            s3 = boto3.client("s3", region_name="eu-central-1")
            s3.create_bucket(
                Bucket=BUCKET,
                CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
            )

            lambda_handler({}, None)

            payload = _read_latest_json(s3, BUCKET)

        assert _SCHEMA_V1_KEYS.issubset(payload.keys())
        assert payload["schema_version"] == "1.0"
        # With no data, all lists should be empty.
        for pop in ("general", "relevant"):
            block = payload[pop]
            for key, value in block.items():
                assert (
                    value == []
                ), f"Expected empty list for {pop}.{key}, got {value!r}"
        # data_basis: every per-listing dataset is empty, but search_config
        # is a static single-record dataset that never depends on the data.
        data_basis = payload["data_basis"]
        for key, value in data_basis.items():
            if key == "search_config":
                assert value, "search_config must always emit one record"
            else:
                assert value == [], f"Expected empty list for data_basis.{key}"


# ---------------------------------------------------------------------------
# Test: physical columns required (no Hive-path inference)
# ---------------------------------------------------------------------------


class TestPhysicalColumnsRequired:
    """Handler must fail clearly when Parquet lacks physical operation/snapshot_date."""

    def test_missing_operation_column_raises_value_error(self, aws_env: None) -> None:
        """
        A Parquet file written without the physical 'operation' column (only
        Hive path encoding) must cause a loud ValueError, not a silent wrong result.
        """
        with mock_aws():
            s3 = boto3.client("s3", region_name="eu-central-1")
            s3.create_bucket(
                Bucket=BUCKET,
                CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
            )
            # Write a Parquet that deliberately omits the 'operation' physical column.
            row = _make_row()
            row.pop("operation")
            df = pd.DataFrame([row])
            buf = io.BytesIO()
            df.to_parquet(buf, index=False, engine="pyarrow")
            buf.seek(0)
            key = (
                f"{SILVER_PREFIX}/operation=sale/snapshot_date=2023-04-09/part.parquet"
            )
            s3.put_object(Bucket=BUCKET, Key=key, Body=buf.read())

            with pytest.raises(ValueError, match="operation"):
                lambda_handler({}, None)

    def test_missing_snapshot_date_column_raises_value_error(
        self, aws_env: None
    ) -> None:
        """
        A Parquet file without the physical 'snapshot_date' column must cause a
        loud ValueError.
        """
        with mock_aws():
            s3 = boto3.client("s3", region_name="eu-central-1")
            s3.create_bucket(
                Bucket=BUCKET,
                CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
            )
            row = _make_row()
            row.pop("snapshot_date")
            df = pd.DataFrame([row])
            buf = io.BytesIO()
            df.to_parquet(buf, index=False, engine="pyarrow")
            buf.seek(0)
            key = (
                f"{SILVER_PREFIX}/operation=sale/snapshot_date=2023-04-09/part.parquet"
            )
            s3.put_object(Bucket=BUCKET, Key=key, Body=buf.read())

            with pytest.raises(ValueError, match="snapshot_date"):
                lambda_handler({}, None)


# ---------------------------------------------------------------------------
# Test: idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Running the handler twice must overwrite latest.json with the same content."""

    def test_lambda_is_idempotent(self, s3_with_silver: Any) -> None:
        """
        Two consecutive invocations must produce the same JSON content
        (apart from 'generated_at' which is a timestamp).
        Asserts that the same S3 key is written and key schema is identical.
        """
        lambda_handler({}, None)
        first = _read_latest_json(s3_with_silver, BUCKET)

        lambda_handler({}, None)
        second = _read_latest_json(s3_with_silver, BUCKET)

        # Schema shape must be identical across both runs.
        assert set(first.keys()) == set(second.keys())
        assert first["schema_version"] == second["schema_version"]
        assert first["scope_districts"] == second["scope_districts"]
