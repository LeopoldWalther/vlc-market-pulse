"""
Unit tests for the Aggregation strategies + GoldAggregator (FEATURE-008).

Includes the acceptance-critical gate: GoldAggregator, run against the 8.1
silver fixture through an ``InMemoryObjectStore``, must reproduce the
committed golden master **byte-for-byte**.
"""

from __future__ import annotations

import io
import json
import os
import sys
from typing import Any, Dict, List
from unittest import mock

import pandas as pd
import pytest

# src/etl on sys.path for `common`, data_processing for flat imports.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from common.object_store import InMemoryObjectStore  # noqa: E402

from gold_aggregator import (  # noqa: E402
    DistrictPriceTimeSeries,
    GoldAggregator,
    GoldResult,
    ListingLocationGridLast3Months,
    NeighborhoodBoxplot,
    NeighborhoodBoxplotLast3Months,
    NeighborhoodPriceTimeSeries,
    PricePerAreaHistogram,
    RentVsSaleRatio,
    RentVsSaleRatioTimeSeries,
    RoomsDistribution,
    SearchConfigDataset,
    SizeHistogram10sqm,
    WeeklyListingVolume,
    default_data_basis,
    default_populations,
)
from tests.test_gold_golden_master import (  # noqa: E402
    _FROZEN_NOW,
    _MIN_COUNT,
    GOLDEN_MASTER_PATH,
    load_silver_fixture,
)

SILVER_PREFIX = "silver/idealista"
GOLD_PREFIX = "gold/aggregations"


def _store_with_silver(df: pd.DataFrame) -> InMemoryObjectStore:
    """Write *df* as one silver Parquet into a fresh in-memory store."""
    store = InMemoryObjectStore()
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False, engine="pyarrow")
    store.put_bytes(
        f"{SILVER_PREFIX}/operation=rent/snapshot_date=2023-04-09/part.parquet",
        buffer.getvalue(),
        content_type="application/octet-stream",
    )
    return store


def make_aggregator(store: InMemoryObjectStore, **kwargs: Any) -> GoldAggregator:
    """Build a GoldAggregator wired against the in-memory fake."""
    return GoldAggregator(
        object_store=store,
        silver_prefix=SILVER_PREFIX,
        gold_prefix=GOLD_PREFIX,
        min_count=_MIN_COUNT,
        **kwargs,
    )


class TestGoldenMasterViaAggregator:
    """The 8.1 golden master gates the strategy-based implementation."""

    def test_aggregate_matches_golden_master_byte_for_byte(self) -> None:
        store = _store_with_silver(load_silver_fixture())

        with mock.patch("gold_aggregate.datetime") as frozen_dt:
            frozen_dt.now.return_value = _FROZEN_NOW
            result = make_aggregator(store).aggregate()

        actual = store.get_bytes(result.key)
        expected = GOLDEN_MASTER_PATH.read_bytes()

        assert actual == expected[:-1], (
            "GoldAggregator output drifted from the committed golden master "
            "— the strategy refactor must be byte-neutral."
        )
        assert result == GoldResult(
            key=f"{GOLD_PREFIX}/latest.json", size_bytes=len(actual)
        )

    def test_count_listings_present_on_price_time_series_datasets(self) -> None:
        """FEATURE-014 (task 14.7): regression guard.

        Documents an existing dependency introduced by the frontend's new
        listing-count-over-time charts (tasks 14.4-14.6): both
        price_time_series_district and price_time_series_neighborhood must
        keep emitting a correct, present ``count_listings`` field per record
        — those charts read it directly, with no backend changes required.
        No production code changes accompany this test.
        """
        store = _store_with_silver(load_silver_fixture())

        with mock.patch("gold_aggregate.datetime") as frozen_dt:
            frozen_dt.now.return_value = _FROZEN_NOW
            result = make_aggregator(store).aggregate()

        document = json.loads(store.get_bytes(result.key))

        district_records = document["general"]["price_time_series_district"]
        neighborhood_records = document["general"]["price_time_series_neighborhood"]

        assert len(district_records) > 0
        assert len(neighborhood_records) > 0

        for record in district_records:
            assert "count_listings" in record
            assert isinstance(record["count_listings"], int)
            assert record["count_listings"] > 0

        for record in neighborhood_records:
            assert "count_listings" in record
            assert isinstance(record["count_listings"], int)
            assert record["count_listings"] > 0

        # Golden-master snapshot check: the first record of each dataset
        # today carries count_listings == 10 (10 rent listings for
        # Ciutat Vella / El Carme on 2023-04-09) — pin the actual value, not
        # just its presence, so a silent aggregation-logic regression (e.g.
        # double-counting or an off-by-one) is caught too.
        assert district_records[0]["count_listings"] == 10
        assert neighborhood_records[0]["count_listings"] == 10


class TestStrategyInterface:
    """Strategies are interchangeable behind the common interface."""

    def test_default_populations_reproduce_schema_v1_keys_in_order(self) -> None:
        general, relevant = default_populations(_MIN_COUNT)

        assert [agg.key for agg in general] == [
            "price_time_series_neighborhood",
            "price_time_series_district",
            "rent_vs_sale_ratio",
            "rent_vs_sale_ratio_time_series",
            "boxplot_by_neighborhood",
            "boxplot_by_neighborhood_last_3m",
        ]
        assert [agg.key for agg in relevant] == [
            "rent_vs_sale_ratio",
            "rent_vs_sale_ratio_time_series",
            "boxplot_by_neighborhood",
            "boxplot_by_neighborhood_last_3m",
        ]

    def test_neighborhood_boxplot_last_3m_key(self) -> None:
        """The new strategy exposes the additive schema key."""
        assert NeighborhoodBoxplotLast3Months().key == (
            "boxplot_by_neighborhood_last_3m"
        )

    def test_neighborhood_boxplot_last_3m_delegates_to_pure_helper(self) -> None:
        """compute() delegates to the pure windowed helper, no duplicated math."""
        strategy = NeighborhoodBoxplotLast3Months(min_count=1)
        df = pd.DataFrame(
            [
                {
                    "operation": "sale",
                    "district": "Extramurs",
                    "neighborhood": "Patraix",
                    "snapshot_date": "2023-04-09",
                    "propertyCode": "P1",
                    "priceByArea": 2000.0,
                    "size": 100.0,
                    "price": 200000.0,
                }
            ]
        )
        records = strategy.compute(df)
        assert len(records) == 1
        assert records[0]["operation"] == "sale"
        assert records[0]["count"] == 1

    @pytest.mark.parametrize(
        "strategy",
        [
            NeighborhoodPriceTimeSeries(),
            DistrictPriceTimeSeries(),
            RentVsSaleRatio(_MIN_COUNT),
            RentVsSaleRatioTimeSeries(_MIN_COUNT),
            NeighborhoodBoxplot(),
        ],
        ids=lambda s: s.key,
    )
    def test_every_strategy_handles_empty_input(self, strategy: Any) -> None:
        """Liskov: all variants accept the same input and return a list."""
        empty = pd.DataFrame(
            columns=[
                "operation",
                "district",
                "neighborhood",
                "snapshot_date",
                "propertyCode",
                "priceByArea",
                "size",
                "price",
            ]
        )
        assert strategy.compute(empty) == []

    def test_new_strategy_plugs_in_without_editing_the_aggregator(self) -> None:
        """Open/Closed: adding a dataset = adding a class, not a switch."""

        class ListingCount:
            key: str = "listing_count"

            def compute(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
                return [{"count": int(len(df))}]

        store = _store_with_silver(load_silver_fixture())
        aggregator = make_aggregator(
            store,
            general_aggregations=(ListingCount(),),
            relevant_aggregations=(),
        )

        result = aggregator.aggregate()
        document = json.loads(store.get_bytes(result.key))

        assert list(document["general"].keys()) == ["listing_count"]
        assert document["general"]["listing_count"][0]["count"] > 0
        assert document["relevant"] == {}


class TestSilverHistoryReading:
    """ObjectStore-backed silver reads with the physical-column contract."""

    def test_empty_store_produces_empty_datasets(self) -> None:
        store = InMemoryObjectStore()

        result = make_aggregator(store).aggregate()
        document = json.loads(store.get_bytes(result.key))

        assert document["schema_version"] == "1.0"
        assert document["general"]["price_time_series_neighborhood"] == []
        assert document["relevant"]["boxplot_by_neighborhood"] == []

    def test_missing_physical_column_raises_value_error(self) -> None:
        store = InMemoryObjectStore()
        df = load_silver_fixture().drop(columns=["snapshot_date"])
        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False, engine="pyarrow")
        store.put_bytes(
            f"{SILVER_PREFIX}/broken/part.parquet",
            buffer.getvalue(),
            content_type="application/octet-stream",
        )

        with pytest.raises(ValueError, match="snapshot_date"):
            make_aggregator(store).aggregate()

    def test_non_parquet_keys_are_ignored(self) -> None:
        store = _store_with_silver(load_silver_fixture())
        store.put_bytes(f"{SILVER_PREFIX}/_SUCCESS", b"", content_type="text/plain")

        result = make_aggregator(store).aggregate()

        assert result.size_bytes > 0


# ---------------------------------------------------------------------------
# Data Basis strategies + wiring (FEATURE-011, task 11.4)
# ---------------------------------------------------------------------------


class TestDefaultDataBasis:
    """default_data_basis() returns the frozen strategy list, in schema order."""

    def test_returns_expected_keys_in_order(self) -> None:
        strategies = default_data_basis()

        assert [agg.key for agg in strategies] == [
            "search_config",
            "weekly_listing_volume",
            "size_histogram_10sqm",
            "rooms_distribution",
            "price_per_area_histogram",
            "listing_location_grid_last_3m",
            "listing_locations_last_3m",
        ]

    @pytest.mark.parametrize(
        "strategy",
        [
            WeeklyListingVolume(),
            SizeHistogram10sqm(),
            RoomsDistribution(),
            PricePerAreaHistogram(),
            ListingLocationGridLast3Months(),
        ],
        ids=lambda s: s.key,
    )
    def test_every_data_basis_strategy_handles_empty_input(self, strategy: Any) -> None:
        """
        Liskov: every per-listing Data Basis strategy accepts an empty df.
        (SearchConfigDataset is excluded — it is the one static, input-
        independent dataset; see TestSearchConfigDatasetIgnoresInput.)
        """
        empty = pd.DataFrame(
            columns=[
                "operation",
                "district",
                "neighborhood",
                "snapshot_date",
                "propertyCode",
                "priceByArea",
                "size",
                "price",
                "rooms",
                "latitude",
                "longitude",
            ]
        )
        assert strategy.compute(empty) == []


class TestSearchConfigDatasetIgnoresInput:
    """SearchConfigDataset's compute() always returns the same static record."""

    def test_returns_single_record_regardless_of_df(self) -> None:
        records = SearchConfigDataset().compute(pd.DataFrame())
        assert len(records) == 1
        assert "center_lat" in records[0]


class TestGoldAggregatorEmitsDataBasis:
    """GoldAggregator.build_document wires the additive data_basis block."""

    def test_document_has_data_basis_key_without_changing_schema_version(
        self,
    ) -> None:
        store = _store_with_silver(load_silver_fixture())

        with mock.patch("gold_aggregate.datetime") as frozen_dt:
            frozen_dt.now.return_value = _FROZEN_NOW
            result = make_aggregator(store).aggregate()

        document = json.loads(store.get_bytes(result.key))
        assert document["schema_version"] == "1.0"
        assert "data_basis" in document

    def test_general_and_relevant_dataset_keys_are_unchanged(self) -> None:
        """H2: adding data_basis must not alter general/relevant dataset keys."""
        store = _store_with_silver(load_silver_fixture())
        result = make_aggregator(store).aggregate()
        document = json.loads(store.get_bytes(result.key))

        assert set(document["general"].keys()) == {
            "price_time_series_neighborhood",
            "price_time_series_district",
            "rent_vs_sale_ratio",
            "rent_vs_sale_ratio_time_series",
            "boxplot_by_neighborhood",
            "boxplot_by_neighborhood_last_3m",
        }
        assert set(document["relevant"].keys()) == {
            "rent_vs_sale_ratio",
            "rent_vs_sale_ratio_time_series",
            "boxplot_by_neighborhood",
            "boxplot_by_neighborhood_last_3m",
        }

    def test_data_basis_respects_scope_districts(self) -> None:
        """
        Data Basis must show the same neighbourhoods as Trend Analysis, not a
        superset (operator decision 2026-07-18): a listing whose district
        falls outside SCOPE_DISTRICTS is excluded from both general/relevant
        AND data_basis.
        """
        store = InMemoryObjectStore()
        buffer = io.BytesIO()
        df = pd.DataFrame(
            [
                {
                    "operation": "sale",
                    "district": "Benimaclet",  # out of scope for general/relevant
                    "neighborhood": "Benimaclet",
                    "snapshot_date": "2023-04-09",
                    "propertyCode": "OUT1",
                    "priceByArea": 2000.0,
                    "size": 100.0,
                    "price": 200_000.0,
                    "floor": "2",
                    "rooms": 2,
                    "bathrooms": 1,
                    "hasLift": True,
                    "latitude": 39.48,
                    "longitude": -0.35,
                }
            ]
        )
        df.to_parquet(buffer, index=False, engine="pyarrow")
        store.put_bytes(
            f"{SILVER_PREFIX}/operation=sale/snapshot_date=2023-04-09/part.parquet",
            buffer.getvalue(),
            content_type="application/octet-stream",
        )

        result = make_aggregator(store).aggregate()
        document = json.loads(store.get_bytes(result.key))

        assert document["general"]["price_time_series_neighborhood"] == []
        assert document["data_basis"]["weekly_listing_volume"] == []

    def test_moto_lambda_aggregation_includes_data_basis(self) -> None:
        """Acceptance criterion: moto-backed Lambda aggregation test confirms
        latest.json includes data_basis (see test_gold_aggregation_lambda.py
        for the dedicated moto/S3 Lambda-level coverage)."""
        store = _store_with_silver(load_silver_fixture())
        result = make_aggregator(store).aggregate()
        document = json.loads(store.get_bytes(result.key))
        assert "data_basis" in document


# ---------------------------------------------------------------------------
# Additive data_basis_relevant population block (FEATURE-016, task 16.5)
# ---------------------------------------------------------------------------


def _relevant_listing(**overrides: Any) -> Dict[str, Any]:
    """A listing that satisfies RELEVANT_FILTER_SPEC unless overridden."""
    base: Dict[str, Any] = {
        "operation": "sale",
        "district": "Extramurs",
        "neighborhood": "Arrancapins",
        "snapshot_date": "2023-04-09",
        "propertyCode": "REL1",
        "priceByArea": 3000.0,
        "size": 130.0,
        "price": 390_000.0,
        "floor": "3",
        "rooms": 3,
        "bathrooms": 2,
        "hasLift": True,
        "latitude": 39.47,
        "longitude": -0.38,
    }
    base.update(overrides)
    return base


def _total(records: List[Dict[str, Any]]) -> int:
    """Sum count_listings over a dataset's records."""
    return sum(int(record["count_listings"]) for record in records)


class TestGoldAggregatorEmitsDataBasisRelevant:
    """build_document adds data_basis_relevant beside data_basis."""

    def _document(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        aggregator = make_aggregator(InMemoryObjectStore())
        return aggregator.build_document(pd.DataFrame(rows))

    def test_block_sits_directly_after_data_basis_with_the_same_datasets(
        self,
    ) -> None:
        document = self._document([_relevant_listing()])

        keys = list(document.keys())
        assert keys.index("data_basis_relevant") == keys.index("data_basis") + 1
        assert list(document["data_basis_relevant"].keys()) == list(
            document["data_basis"].keys()
        )
        assert document["schema_version"] == "1.0"

    def test_block_only_counts_rows_matching_the_relevant_filter(self) -> None:
        rows = [
            _relevant_listing(propertyCode="REL1"),
            _relevant_listing(propertyCode="REL2", neighborhood="La Roqueta"),
            # Not relevant: too small and no lift.
            _relevant_listing(propertyCode="GEN1", size=80.0, hasLift=False),
        ]
        document = self._document(rows)

        for dataset in (
            "weekly_listing_volume",
            "size_histogram_10sqm",
            "rooms_distribution",
            "price_per_area_histogram",
        ):
            assert _total(document["data_basis"][dataset]) == 3, dataset
            assert _total(document["data_basis_relevant"][dataset]) == 2, dataset
        assert len(document["data_basis"]["listing_locations_last_3m"]) == 3
        assert len(document["data_basis_relevant"]["listing_locations_last_3m"]) == 2

    def test_search_config_is_identical_in_both_blocks(self) -> None:
        document = self._document([_relevant_listing()])

        assert (
            document["data_basis_relevant"]["search_config"]
            == document["data_basis"]["search_config"]
        )

    def test_empty_silver_history_yields_empty_datasets_in_both_blocks(self) -> None:
        store = InMemoryObjectStore()

        result = make_aggregator(store).aggregate()
        document = json.loads(store.get_bytes(result.key))

        for block in ("data_basis", "data_basis_relevant"):
            for key, value in document[block].items():
                if key != "search_config":
                    assert value == [], f"{block}.{key}"

    def test_no_relevant_rows_yields_empty_relevant_datasets(self) -> None:
        document = self._document([_relevant_listing(size=80.0)])

        assert _total(document["data_basis"]["weekly_listing_volume"]) == 1
        assert document["data_basis_relevant"]["weekly_listing_volume"] == []
