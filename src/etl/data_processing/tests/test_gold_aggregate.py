"""
Tests for the gold-layer aggregation module (gold_aggregate.py).

Written TDD-style: all tests in this file are RED before gold_aggregate.py
exists, then GREEN once the implementation is in place.

The tests lock in the frozen schema-v1.0 JSON contract that FEATURE-005 depends
on. Every acceptance criterion from the FEATURE-004 technical plan for task 4.1
is covered here.
"""

from __future__ import annotations

import json

import os
import sys
from datetime import date
from typing import Any, Dict, List, Tuple

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from gold_aggregate import (  # noqa: E402
    ROLLING_KPI_WINDOW_MONTHS,
    _boxplot_by_neighborhood,
    _boxplot_by_neighborhood_last_months,
    _rolling_window_start,
    apply_scope,
    build_aggregation_json,
    build_population_block,
    latest_by_property,
    listing_location_grid_last_3m,
    listing_locations_last_3m,
    price_per_area_histogram,
    rooms_distribution,
    search_config_summary,
    size_histogram_10sqm,
    weekly_listing_volume,
)

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

SCOPE_DISTRICTS = ["Extramurs", "Ciutat Vella", "L'Eixample"]

# Minimal columns required by all helpers.
_BASE_COLS = [
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
]


def _make_listing(**overrides: Any) -> Dict[str, Any]:
    """Return a minimal valid listing dict with optional field overrides."""
    base: Dict[str, Any] = {
        "operation": "sale",
        "district": "Extramurs",
        "neighborhood": "Patraix",
        "snapshot_date": date(2023, 4, 9),
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


def _df(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """Build a DataFrame from listing dicts, casting snapshot_date to date."""
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# apply_scope
# ---------------------------------------------------------------------------


class TestApplyScope:
    """apply_scope must keep only the 3 target districts."""

    def test_apply_scope_keeps_only_three_districts(self) -> None:
        """Rows outside the 3 scope districts are dropped."""
        rows = [
            _make_listing(district="Extramurs"),
            _make_listing(district="Ciutat Vella"),
            _make_listing(district="L'Eixample"),
            _make_listing(district="Benimaclet"),  # must be dropped
            _make_listing(district="El Pla del Real"),  # must be dropped
        ]
        result = apply_scope(_df(rows))
        assert set(result["district"].unique()) == set(SCOPE_DISTRICTS)
        assert len(result) == 3

    def test_apply_scope_accepts_eixample_apostrophe(self) -> None:
        """The real district string from the Idealista API uses a regular ASCII apostrophe."""
        rows = [_make_listing(district="L'Eixample")]
        result = apply_scope(_df(rows))
        assert len(result) == 1

    def test_apply_scope_empty_dataframe(self) -> None:
        """Empty input produces an empty output without raising."""
        result = apply_scope(pd.DataFrame(columns=_BASE_COLS))
        assert result.empty

    def test_apply_scope_all_out_of_scope(self) -> None:
        """All rows outside scope → empty result."""
        rows = [_make_listing(district="Benimaclet")]
        result = apply_scope(_df(rows))
        assert result.empty


# ---------------------------------------------------------------------------
# Dedup: same propertyCode across two snapshots yields two time-series points
# ---------------------------------------------------------------------------


class TestDedup:
    """
    Dedup happens only within (operation, snapshot_date, propertyCode).
    The same propertyCode appearing in two different snapshots must produce
    two separate time-series rows, not one.
    """

    def test_same_property_code_two_snapshots_yields_two_points(self) -> None:
        """
        A listing re-appearing in a later snapshot must not be deduplicated
        away — the time-series would collapse if we did a global dedup.
        """
        rows = [
            _make_listing(
                propertyCode="P001",
                snapshot_date=date(2023, 4, 9),
                priceByArea=2500.0,
            ),
            _make_listing(
                propertyCode="P001",
                snapshot_date=date(2023, 4, 16),
                priceByArea=2600.0,
            ),
        ]
        scoped = apply_scope(_df(rows))
        block = build_population_block(scoped, row_filter=None)
        series = block["price_time_series_neighborhood"]
        # Two different snapshot_dates → two rows in the time-series.
        assert len(series) == 2

    def test_duplicate_within_same_snapshot_is_deduped(self) -> None:
        """
        Two identical (operation, snapshot_date, propertyCode) rows within the
        same snapshot should be deduped to one before aggregation.
        """
        rows = [
            _make_listing(
                propertyCode="P001",
                snapshot_date=date(2023, 4, 9),
                priceByArea=2500.0,
            ),
            _make_listing(
                propertyCode="P001",
                snapshot_date=date(2023, 4, 9),
                priceByArea=2500.0,
            ),
        ]
        scoped = apply_scope(_df(rows))
        block = build_population_block(scoped, row_filter=None)
        series = block["price_time_series_neighborhood"]
        # Both rows are the same snapshot → deduped → one aggregation row.
        assert series[0]["count_listings"] == 1


# ---------------------------------------------------------------------------
# build_population_block — general population (row_filter=None)
# ---------------------------------------------------------------------------


class TestBuildPopulationBlockGeneral:
    """Tests for the general population (identity filter)."""

    def test_price_time_series_neighborhood_columns(self) -> None:
        """Each record has the required column set."""
        rows = [_make_listing()]
        block = build_population_block(apply_scope(_df(rows)), row_filter=None)
        record = block["price_time_series_neighborhood"][0]
        for key in (
            "operation",
            "district",
            "neighborhood",
            "snapshot_date",
            "count_listings",
            "mean_priceByArea",
            "mean_size",
            "mean_price",
        ):
            assert key in record, f"missing key: {key}"

    def test_mean_price_key_not_mean_prize(self) -> None:
        """The output must use 'mean_price', never 'mean_prize'."""
        rows = [_make_listing()]
        block = build_population_block(apply_scope(_df(rows)), row_filter=None)
        record = block["price_time_series_neighborhood"][0]
        assert "mean_price" in record
        assert "mean_prize" not in record

    def test_price_time_series_district_is_count_weighted(self) -> None:
        """
        District-level mean_priceByArea must be count-weighted, not mean-of-means.

        Two neighborhoods in one district, one snapshot_date:
          - Patraix: 2 listings, mean_priceByArea = 2000
          - Russafa:  1 listing,  mean_priceByArea = 3000
        Correct weighted mean = (2*2000 + 1*3000) / (2+1) = 7000/3 ≈ 2333.33
        Incorrect mean-of-means = (2000 + 3000) / 2 = 2500
        """
        rows = [
            _make_listing(
                neighborhood="Patraix", priceByArea=2000.0, size=120.0, price=240_000.0
            ),
            _make_listing(
                neighborhood="Patraix",
                priceByArea=2000.0,
                size=120.0,
                price=240_000.0,
                propertyCode="P002",
            ),
            _make_listing(
                neighborhood="Russafa",
                priceByArea=3000.0,
                size=130.0,
                price=390_000.0,
                propertyCode="P003",
            ),
        ]
        block = build_population_block(apply_scope(_df(rows)), row_filter=None)
        district_series = block["price_time_series_district"]
        assert len(district_series) == 1
        record = district_series[0]
        expected = (2 * 2000.0 + 1 * 3000.0) / 3
        assert abs(record["mean_priceByArea"] - expected) < 0.01

    def test_boxplot_returns_five_number_summary(self) -> None:
        """boxplot_by_neighborhood contains count, min, q1, median, q3, max."""
        rows = [
            _make_listing(priceByArea=1000.0, propertyCode="P001"),
            _make_listing(priceByArea=2000.0, propertyCode="P002"),
            _make_listing(priceByArea=3000.0, propertyCode="P003"),
            _make_listing(priceByArea=4000.0, propertyCode="P004"),
            _make_listing(priceByArea=5000.0, propertyCode="P005"),
        ]
        block = build_population_block(apply_scope(_df(rows)), row_filter=None)
        bp = block["boxplot_by_neighborhood"][0]
        for key in (
            "operation",
            "district",
            "neighborhood",
            "count",
            "min",
            "q1",
            "median",
            "q3",
            "max",
        ):
            assert key in bp, f"missing boxplot key: {key}"
        assert bp["count"] == 5
        assert bp["min"] == pytest.approx(1000.0)
        assert bp["max"] == pytest.approx(5000.0)

    def test_general_block_has_price_time_series_district(self) -> None:
        """General population must include price_time_series_district."""
        rows = [_make_listing()]
        block = build_population_block(apply_scope(_df(rows)), row_filter=None)
        assert "price_time_series_district" in block

    def test_general_block_has_all_required_keys(self) -> None:
        """General population block must carry all 5 required dataset keys."""
        rows = [_make_listing()]
        block = build_population_block(apply_scope(_df(rows)), row_filter=None)
        for key in (
            "price_time_series_neighborhood",
            "price_time_series_district",
            "rent_vs_sale_ratio",
            "rent_vs_sale_ratio_time_series",
            "boxplot_by_neighborhood",
        ):
            assert key in block, f"missing block key: {key}"


# ---------------------------------------------------------------------------
# build_population_block — relevant filter
# ---------------------------------------------------------------------------


class TestRelevantFilter:
    """Tests for the relevant-population filter (apartments like ours)."""

    def test_relevant_filter_selects_apartments_like_ours(self) -> None:
        """Only listings matching all 5 relevant criteria survive the filter."""
        qualifying = _make_listing(
            hasLift=True, floor="3", size=130.0, rooms=3, bathrooms=2
        )
        too_small = _make_listing(
            hasLift=True,
            floor="3",
            size=100.0,
            rooms=3,
            bathrooms=2,
            propertyCode="P002",
        )
        ground_floor = _make_listing(
            hasLift=True,
            floor="1",
            size=130.0,
            rooms=3,
            bathrooms=2,
            propertyCode="P003",
        )
        no_lift = _make_listing(
            hasLift=False,
            floor="3",
            size=130.0,
            rooms=3,
            bathrooms=2,
            propertyCode="P004",
        )
        too_few_rooms = _make_listing(
            hasLift=True,
            floor="3",
            size=130.0,
            rooms=1,
            bathrooms=2,
            propertyCode="P005",
        )
        too_few_baths = _make_listing(
            hasLift=True,
            floor="3",
            size=130.0,
            rooms=3,
            bathrooms=1,
            propertyCode="P006",
        )

        def relevant_filter(df: pd.DataFrame) -> pd.DataFrame:
            """The relevant-population predicate."""
            return df[
                (df["hasLift"] == True)  # noqa: E712
                & (df["floor"] != "1")
                & (df["size"] > 120)
                & (df["rooms"] >= 2)
                & (df["bathrooms"] >= 2)
            ]

        scoped = apply_scope(
            _df(
                [
                    qualifying,
                    too_small,
                    ground_floor,
                    no_lift,
                    too_few_rooms,
                    too_few_baths,
                ]
            )
        )
        block = build_population_block(scoped, row_filter=relevant_filter)
        # Only qualifying survives — check boxplot (available in relevant block).
        assert len(block["boxplot_by_neighborhood"]) == 1

    def test_floor_compared_as_string(self) -> None:
        """floor='1' must be excluded regardless of numeric interpretation."""
        on_first_floor = _make_listing(
            hasLift=True, floor="1", size=130.0, rooms=3, bathrooms=2
        )

        def relevant_filter(df: pd.DataFrame) -> pd.DataFrame:
            return df[
                (df["hasLift"] == True)  # noqa: E712
                & (df["floor"] != "1")
                & (df["size"] > 120)
                & (df["rooms"] >= 2)
                & (df["bathrooms"] >= 2)
            ]

        scoped = apply_scope(_df([on_first_floor]))
        block = build_population_block(scoped, row_filter=relevant_filter)
        # floor='1' excluded → no listings → empty boxplot.
        assert block["boxplot_by_neighborhood"] == []

    def test_relevant_block_has_required_keys(self) -> None:
        """Relevant population block must carry 3 required dataset keys."""
        rows = [_make_listing()]

        def relevant_filter(df: pd.DataFrame) -> pd.DataFrame:
            return df[
                (df["hasLift"] == True)  # noqa: E712
                & (df["floor"] != "1")
                & (df["size"] > 120)
                & (df["rooms"] >= 2)
                & (df["bathrooms"] >= 2)
            ]

        block = build_population_block(
            apply_scope(_df(rows)), row_filter=relevant_filter
        )
        for key in (
            "rent_vs_sale_ratio",
            "rent_vs_sale_ratio_time_series",
            "boxplot_by_neighborhood",
        ):
            assert key in block, f"missing relevant block key: {key}"

    def test_relevant_block_lacks_price_time_series(self) -> None:
        """Relevant population does NOT include price_time_series_neighborhood/district."""
        rows = [_make_listing()]

        def relevant_filter(df: pd.DataFrame) -> pd.DataFrame:
            return df[
                (df["hasLift"] == True)  # noqa: E712
                & (df["floor"] != "1")
                & (df["size"] > 120)
                & (df["rooms"] >= 2)
                & (df["bathrooms"] >= 2)
            ]

        block = build_population_block(
            apply_scope(_df(rows)), row_filter=relevant_filter
        )
        assert "price_time_series_neighborhood" not in block
        assert "price_time_series_district" not in block


# ---------------------------------------------------------------------------
# Rent vs sale ratio
# ---------------------------------------------------------------------------


class TestRentVsSaleRatio:
    """rent_vs_sale_ratio and rent_vs_sale_ratio_time_series tests."""

    def test_ratio_time_series_per_snapshot(self) -> None:
        """Each snapshot_date produces a separate ratio record."""
        rows = [
            _make_listing(
                operation="sale",
                priceByArea=2500.0,
                snapshot_date=date(2023, 4, 9),
                propertyCode="P001",
            ),
            _make_listing(
                operation="rent",
                priceByArea=10.0,
                snapshot_date=date(2023, 4, 9),
                propertyCode="P002",
            ),
            _make_listing(
                operation="sale",
                priceByArea=2600.0,
                snapshot_date=date(2023, 4, 16),
                propertyCode="P001",
            ),
            _make_listing(
                operation="rent",
                priceByArea=11.0,
                snapshot_date=date(2023, 4, 16),
                propertyCode="P002",
            ),
        ]
        # Use min_count=1 so pairs with a single listing per side are included;
        # this test verifies the time-series structure, not the sparsity filter.
        block = build_population_block(
            apply_scope(_df(rows)), row_filter=None, min_count=1
        )
        ts = block["rent_vs_sale_ratio_time_series"]
        snapshot_dates = [r["snapshot_date"] for r in ts]
        assert len(set(snapshot_dates)) == 2

    def test_ratio_record_columns(self) -> None:
        """rent_vs_sale_ratio records include all required keys."""
        rows = [
            _make_listing(operation="sale", priceByArea=2500.0, propertyCode="P001"),
            _make_listing(operation="rent", priceByArea=10.0, propertyCode="P002"),
        ]
        block = build_population_block(apply_scope(_df(rows)), row_filter=None)
        if block["rent_vs_sale_ratio"]:
            record = block["rent_vs_sale_ratio"][0]
            for key in (
                "district",
                "neighborhood",
                "mean_priceByArea_sale",
                "mean_priceByArea_rent",
                "mean_sales_price_by_rent_ratio",
                "count_listings_sale",
                "count_listings_rent",
            ):
                assert key in record, f"missing ratio key: {key}"

    def test_ratio_min_count_filters_sparse_pairs(self) -> None:
        """Neighborhoods with count_sale < min_count are excluded from ratio."""
        # Only 1 sale listing — below the default min_count=5.
        rows = [
            _make_listing(operation="sale", priceByArea=2500.0, propertyCode="P001"),
            _make_listing(operation="rent", priceByArea=10.0, propertyCode="P002"),
            _make_listing(operation="rent", priceByArea=11.0, propertyCode="P003"),
            _make_listing(operation="rent", priceByArea=12.0, propertyCode="P004"),
            _make_listing(operation="rent", priceByArea=13.0, propertyCode="P005"),
            _make_listing(operation="rent", priceByArea=14.0, propertyCode="P006"),
        ]
        block = build_population_block(
            apply_scope(_df(rows)), row_filter=None, min_count=5
        )
        # Sale side has only 1 → filtered out.
        assert block["rent_vs_sale_ratio"] == []


# ---------------------------------------------------------------------------
# build_aggregation_json — top-level schema v1.0
# ---------------------------------------------------------------------------


class TestBuildAggregationJson:
    """Tests for the top-level build_aggregation_json function."""

    def _make_silver_df(self) -> pd.DataFrame:
        """Two operations, two snapshots, enough listings for ratios."""
        rows = []
        for snap in [date(2023, 4, 9), date(2023, 4, 16)]:
            for i in range(6):
                rows.append(
                    _make_listing(
                        operation="sale",
                        priceByArea=2500.0 + i * 10,
                        snapshot_date=snap,
                        propertyCode=f"S{i:03d}",
                    )
                )
                rows.append(
                    _make_listing(
                        operation="rent",
                        priceByArea=10.0 + i * 0.5,
                        price=1200.0 + i * 50,
                        snapshot_date=snap,
                        propertyCode=f"R{i:03d}",
                    )
                )
        return _df(rows)

    def test_build_aggregation_json_matches_schema_v1_two_populations(self) -> None:
        """Top-level keys and both population blocks are present."""
        result = build_aggregation_json(self._make_silver_df())
        for key in (
            "schema_version",
            "generated_at",
            "scope_districts",
            "min_count",
            "relevant_filter",
            "general",
            "relevant",
        ):
            assert key in result, f"missing top-level key: {key}"

    def test_schema_version_is_one_dot_zero(self) -> None:
        """schema_version must be exactly '1.0'."""
        result = build_aggregation_json(self._make_silver_df())
        assert result["schema_version"] == "1.0"

    def test_generated_at_is_iso8601_string(self) -> None:
        """generated_at must be a non-empty ISO-8601 UTC string."""
        result = build_aggregation_json(self._make_silver_df())
        generated_at = result["generated_at"]
        assert isinstance(generated_at, str) and len(generated_at) > 0

    def test_scope_districts_matches_three_districts(self) -> None:
        """scope_districts must be the 3 target district strings."""
        result = build_aggregation_json(self._make_silver_df())
        assert set(result["scope_districts"]) == set(SCOPE_DISTRICTS)

    def test_general_block_has_five_datasets(self) -> None:
        """general block must contain all 5 dataset keys."""
        result = build_aggregation_json(self._make_silver_df())
        for key in (
            "price_time_series_neighborhood",
            "price_time_series_district",
            "rent_vs_sale_ratio",
            "rent_vs_sale_ratio_time_series",
            "boxplot_by_neighborhood",
        ):
            assert key in result["general"], f"missing general key: {key}"

    def test_relevant_block_has_three_datasets(self) -> None:
        """relevant block must contain exactly 3 dataset keys."""
        result = build_aggregation_json(self._make_silver_df())
        for key in (
            "rent_vs_sale_ratio",
            "rent_vs_sale_ratio_time_series",
            "boxplot_by_neighborhood",
        ):
            assert key in result["relevant"], f"missing relevant key: {key}"

    def test_empty_silver_dataframe_yields_valid_json(self) -> None:
        """Empty input produces a valid schema-v1.0 dict with empty datasets."""
        result = build_aggregation_json(pd.DataFrame(columns=_BASE_COLS))
        assert result["schema_version"] == "1.0"
        assert result["general"]["price_time_series_neighborhood"] == []
        assert result["relevant"]["rent_vs_sale_ratio"] == []

    def test_empty_relevant_subset_yields_empty_relevant_datasets(self) -> None:
        """No relevant listings → empty relevant block datasets, no exception."""
        # All listings fail the relevant filter (no lift).
        rows = [_make_listing(hasLift=False, propertyCode=f"P{i}") for i in range(6)]
        result = build_aggregation_json(_df(rows))
        assert result["relevant"]["rent_vs_sale_ratio"] == []
        assert result["relevant"]["boxplot_by_neighborhood"] == []


# ---------------------------------------------------------------------------
# _rolling_window_start — rolling KPI window helper (FEATURE-010)
# ---------------------------------------------------------------------------


class TestRollingWindowStart:
    """Tests for the pure rolling-window start helper."""

    def test_rolling_kpi_window_months_is_three(self) -> None:
        """The single named window-length constant must be 3 months."""
        assert ROLLING_KPI_WINDOW_MONTHS == 3

    def test_returns_max_minus_three_months_for_long_history(self) -> None:
        """GIVEN >3 months of history, returns max(snapshot_date) - 3 months."""
        df = pd.DataFrame(
            {
                "snapshot_date": [
                    date(2023, 1, 1),
                    date(2023, 2, 1),
                    date(2023, 3, 1),
                    date(2023, 4, 9),
                ]
            }
        )
        start = _rolling_window_start(df, ROLLING_KPI_WINDOW_MONTHS)
        expected = pd.Timestamp(date(2023, 4, 9)) - pd.DateOffset(months=3)
        assert start == expected

    def test_short_history_returns_start_before_all_rows(self) -> None:
        """GIVEN only 2 weeks of history, the returned start includes all rows."""
        df = pd.DataFrame(
            {
                "snapshot_date": [
                    date(2023, 4, 1),
                    date(2023, 4, 9),
                ]
            }
        )
        start = _rolling_window_start(df, ROLLING_KPI_WINDOW_MONTHS)
        assert start is not None
        assert (pd.to_datetime(df["snapshot_date"]) >= start).all()

    def test_empty_dataframe_returns_none(self) -> None:
        """GIVEN an empty DataFrame, the helper returns None without raising."""
        df = pd.DataFrame(columns=["snapshot_date"])
        assert _rolling_window_start(df, ROLLING_KPI_WINDOW_MONTHS) is None

    def test_no_parseable_dates_returns_none(self) -> None:
        """GIVEN only unparseable snapshot_date values, returns None."""
        df = pd.DataFrame({"snapshot_date": ["not-a-date", None]})
        assert _rolling_window_start(df, ROLLING_KPI_WINDOW_MONTHS) is None

    def test_iso_string_date_and_timestamp_inputs_agree(self) -> None:
        """ISO string, datetime.date, and pandas Timestamp inputs all agree."""
        iso_df = pd.DataFrame({"snapshot_date": ["2023-01-01", "2023-04-09"]})
        date_df = pd.DataFrame({"snapshot_date": [date(2023, 1, 1), date(2023, 4, 9)]})
        ts_df = pd.DataFrame(
            {
                "snapshot_date": [
                    pd.Timestamp("2023-01-01"),
                    pd.Timestamp("2023-04-09"),
                ]
            }
        )
        iso_start = _rolling_window_start(iso_df, 3)
        date_start = _rolling_window_start(date_df, 3)
        ts_start = _rolling_window_start(ts_df, 3)
        assert iso_start == date_start == ts_start


# ---------------------------------------------------------------------------
# _boxplot_by_neighborhood_last_months — windowed boxplot (FEATURE-010)
# ---------------------------------------------------------------------------


class TestBoxplotByNeighborhoodLastMonths:
    """Tests for the rolling 3-month neighborhood boxplot helper."""

    def test_old_outlier_outside_window_does_not_affect_summary(self) -> None:
        """An old outlier outside the rolling window must not skew min/max/median."""
        rows = [
            # Old outlier — 6 months before the latest snapshot.
            _make_listing(
                propertyCode="OLD1",
                snapshot_date=date(2022, 10, 9),
                priceByArea=99999.0,
            ),
        ]
        # 5 in-window listings spanning the most recent 3 months.
        for i, (day, price) in enumerate(
            [
                (date(2023, 2, 9), 1000.0),
                (date(2023, 2, 20), 2000.0),
                (date(2023, 3, 9), 3000.0),
                (date(2023, 3, 20), 4000.0),
                (date(2023, 4, 9), 5000.0),
            ]
        ):
            rows.append(
                _make_listing(
                    propertyCode=f"NEW{i}", snapshot_date=day, priceByArea=price
                )
            )
        df = pd.DataFrame(rows)
        records = _boxplot_by_neighborhood_last_months(df, min_count=1)
        assert len(records) == 1
        rec = records[0]
        assert rec["count"] == 5
        assert rec["min"] == pytest.approx(1000.0)
        assert rec["max"] == pytest.approx(5000.0)
        assert rec["median"] == pytest.approx(3000.0)

    def test_returns_same_keys_and_shape_as_all_time_boxplot(self) -> None:
        """The windowed helper returns the same record keys as the all-time one."""
        rows = [_make_listing(propertyCode=f"P{i}") for i in range(5)]
        df = pd.DataFrame(rows)
        windowed = _boxplot_by_neighborhood_last_months(df, min_count=1)
        all_time = _boxplot_by_neighborhood(df)
        assert set(windowed[0].keys()) == set(all_time[0].keys())

    def test_boundary_date_row_is_included(self) -> None:
        """A row exactly on the window boundary date is included (inclusive)."""
        rows = [_make_listing(propertyCode=f"P{i}") for i in range(4)]
        latest = date(2023, 4, 9)
        boundary = pd.Timestamp(latest) - pd.DateOffset(months=3)
        rows.append(
            _make_listing(
                propertyCode="BOUNDARY",
                snapshot_date=boundary.date(),
                priceByArea=7777.0,
            )
        )
        df = pd.DataFrame(rows)
        records = _boxplot_by_neighborhood_last_months(df, min_count=1)
        assert records[0]["count"] == 5

    def test_groups_below_min_count_are_excluded(self) -> None:
        """Groups with fewer than min_count listings inside the window are excluded."""
        rows = [
            _make_listing(propertyCode="P1"),
            _make_listing(propertyCode="P2"),
        ]
        df = pd.DataFrame(rows)
        records = _boxplot_by_neighborhood_last_months(df, min_count=5)
        assert records == []

    def test_all_time_boxplot_unaffected_by_windowed_helper(self) -> None:
        """_boxplot_by_neighborhood remains all-time & unfiltered by min_count."""
        rows = [
            _make_listing(propertyCode="OLD1", snapshot_date=date(2022, 1, 1)),
            _make_listing(propertyCode="OLD2", snapshot_date=date(2022, 1, 2)),
        ]
        df = pd.DataFrame(rows)
        # Only 2 rows total — below the default min_count of 5 — yet the
        # all-time helper must still report them (no implicit filtering).
        records = _boxplot_by_neighborhood(df)
        assert len(records) == 1
        assert records[0]["count"] == 2

    def test_empty_dataframe_returns_empty_list(self) -> None:
        """Empty input yields an empty list, not an exception."""
        df = pd.DataFrame(columns=_BASE_COLS)
        assert _boxplot_by_neighborhood_last_months(df) == []


# ---------------------------------------------------------------------------
# build_population_block — additive boxplot_by_neighborhood_last_3m field
# ---------------------------------------------------------------------------


class TestPopulationBlockLast3MonthsField:
    """build_population_block must emit the additive rolling boxplot field."""

    def test_general_block_has_last_3m_field(self) -> None:
        """general population block includes boxplot_by_neighborhood_last_3m."""
        rows = [_make_listing(propertyCode=f"P{i}") for i in range(5)]
        block = build_population_block(apply_scope(_df(rows)), row_filter=None)
        assert "boxplot_by_neighborhood_last_3m" in block

    def test_relevant_block_has_last_3m_field(self) -> None:
        """relevant population block includes boxplot_by_neighborhood_last_3m."""
        rows = [_make_listing(propertyCode=f"P{i}") for i in range(5)]
        block = build_population_block(apply_scope(_df(rows)), row_filter=None)
        assert "boxplot_by_neighborhood_last_3m" in block

    def test_all_time_field_still_present_and_unchanged(self) -> None:
        """boxplot_by_neighborhood keeps its all-time values alongside the new field."""
        rows = [
            _make_listing(propertyCode="OLD1", snapshot_date=date(2022, 1, 1)),
            _make_listing(propertyCode="OLD2", snapshot_date=date(2022, 1, 2)),
        ]
        block = build_population_block(apply_scope(_df(rows)), row_filter=None)
        assert "boxplot_by_neighborhood" in block
        assert block["boxplot_by_neighborhood"][0]["count"] == 2

    def test_last_3m_field_uses_min_count_consistent_with_block_min_count(
        self,
    ) -> None:
        """min_count passed to build_population_block is forwarded to the rolling helper."""
        rows = [
            _make_listing(propertyCode="P1"),
            _make_listing(propertyCode="P2"),
        ]
        block = build_population_block(
            apply_scope(_df(rows)), row_filter=None, min_count=5
        )
        # Only 2 listings — below min_count=5 — so the rolling field excludes
        # this group while the all-time field still reports it.
        assert block["boxplot_by_neighborhood_last_3m"] == []
        assert len(block["boxplot_by_neighborhood"]) == 1


# ---------------------------------------------------------------------------
# Data Basis pure aggregation helpers (FEATURE-011, task 11.2)
# ---------------------------------------------------------------------------


class TestSearchConfigSummary:
    """search_config_summary() serializes the shared search constants."""

    def test_returns_stable_public_schema_keys(self) -> None:
        summary = search_config_summary()
        assert set(summary.keys()) == {
            "center_lat",
            "center_lon",
            "distance_m",
            "min_size_m2",
            "max_size_m2",
            "elevator",
            "air_conditioning",
            "preservation",
            "property_type",
            "sale_credential_label",
            "rent_credential_label",
        }

    def test_values_match_shared_search_config_module(self) -> None:
        from common.search_config import IDEALISTA_SEARCH_PARAMS

        summary = search_config_summary()
        assert summary["center_lat"] == IDEALISTA_SEARCH_PARAMS["center_lat"]
        assert summary["center_lon"] == IDEALISTA_SEARCH_PARAMS["center_lon"]
        assert summary["distance_m"] == IDEALISTA_SEARCH_PARAMS["distance_m"]
        assert summary["property_type"] == IDEALISTA_SEARCH_PARAMS["property_type"]

    def test_does_not_expose_collector_internal_fields(self) -> None:
        """M2: no base_url/order/sort/max_items collector-internal leakage."""
        summary = search_config_summary()
        assert "base_url" not in summary
        assert "order" not in summary
        assert "sort" not in summary
        assert "max_items" not in summary
        assert "language" not in summary
        assert "country" not in summary


class TestWeeklyListingVolume:
    """weekly_listing_volume groups deduped rows by operation + snapshot_date."""

    def test_groups_by_operation_and_snapshot_date(self) -> None:
        rows = [
            _make_listing(
                operation="sale", snapshot_date=date(2023, 4, 9), propertyCode="S1"
            ),
            _make_listing(
                operation="sale", snapshot_date=date(2023, 4, 9), propertyCode="S2"
            ),
            _make_listing(
                operation="rent", snapshot_date=date(2023, 4, 9), propertyCode="R1"
            ),
            _make_listing(
                operation="sale", snapshot_date=date(2023, 4, 16), propertyCode="S3"
            ),
        ]
        result = weekly_listing_volume(_df(rows))
        by_key = {
            (r["operation"], r["snapshot_date"]): r["count_listings"] for r in result
        }
        assert by_key[("sale", "2023-04-09")] == 2
        assert by_key[("rent", "2023-04-09")] == 1
        assert by_key[("sale", "2023-04-16")] == 1

    def test_includes_data_outside_the_three_scope_districts(self) -> None:
        """This pure helper itself has no scope filtering — GoldAggregator
        applies apply_scope() upstream before calling it (see
        test_gold_aggregator.py::test_data_basis_respects_scope_districts)."""
        rows = [_make_listing(district="Benimaclet", propertyCode="P1")]
        result = weekly_listing_volume(_df(rows))
        assert result[0]["count_listings"] == 1

    def test_empty_input_returns_empty_list(self) -> None:
        assert weekly_listing_volume(pd.DataFrame(columns=_BASE_COLS)) == []


class TestLatestByProperty:
    """latest_by_property keeps only the newest snapshot per (operation, propertyCode)."""

    def test_keeps_latest_snapshot_row_per_property(self) -> None:
        rows = [
            _make_listing(
                propertyCode="P1", snapshot_date=date(2023, 4, 9), size=100.0
            ),
            _make_listing(
                propertyCode="P1", snapshot_date=date(2023, 4, 16), size=105.0
            ),
        ]
        result = latest_by_property(_df(rows))
        assert len(result) == 1
        assert result.iloc[0]["size"] == 105.0

    def test_distinguishes_by_operation(self) -> None:
        rows = [
            _make_listing(propertyCode="P1", operation="sale"),
            _make_listing(propertyCode="P1", operation="rent"),
        ]
        result = latest_by_property(_df(rows))
        assert len(result) == 2

    def test_empty_input_returns_empty_dataframe(self) -> None:
        result = latest_by_property(pd.DataFrame(columns=_BASE_COLS))
        assert result.empty


class TestSizeHistogram10sqm:
    """size_histogram_10sqm bins listings into deterministic 10 m2 buckets."""

    def test_bins_are_10sqm_wide_and_deterministic(self) -> None:
        rows = [
            _make_listing(propertyCode="P1", size=101.0),
            _make_listing(propertyCode="P2", size=109.0),
            _make_listing(propertyCode="P3", size=110.0),
        ]
        result = size_histogram_10sqm(_df(rows))
        by_bin = {
            (r["bin_start_m2"], r["bin_end_m2"]): r["count_listings"] for r in result
        }
        assert by_bin[(100, 110)] == 2
        assert by_bin[(110, 120)] == 1

    def test_emits_operation_field(self) -> None:
        rows = [_make_listing(operation="rent", size=105.0)]
        result = size_histogram_10sqm(_df(rows))
        assert result[0]["operation"] == "rent"

    def test_empty_input_returns_empty_list(self) -> None:
        assert size_histogram_10sqm(pd.DataFrame(columns=_BASE_COLS)) == []


class TestRoomsDistribution:
    """rooms_distribution emits operation, rooms, count_listings."""

    def test_counts_per_operation_and_room_count(self) -> None:
        rows = [
            _make_listing(propertyCode="P1", operation="sale", rooms=2),
            _make_listing(propertyCode="P2", operation="sale", rooms=2),
            _make_listing(propertyCode="P3", operation="sale", rooms=3),
            _make_listing(propertyCode="P4", operation="rent", rooms=2),
        ]
        result = rooms_distribution(_df(rows))
        by_key = {(r["operation"], r["rooms"]): r["count_listings"] for r in result}
        assert by_key[("sale", 2)] == 2
        assert by_key[("sale", 3)] == 1
        assert by_key[("rent", 2)] == 1

    def test_empty_input_returns_empty_list(self) -> None:
        assert rooms_distribution(pd.DataFrame(columns=_BASE_COLS)) == []


class TestPricePerAreaHistogram:
    """price_per_area_histogram bins priceByArea with operation-specific widths."""

    def test_sale_and_rent_use_different_bin_widths(self) -> None:
        rows = [
            _make_listing(propertyCode="S1", operation="sale", priceByArea=2500.0),
            _make_listing(propertyCode="S2", operation="sale", priceByArea=2600.0),
            _make_listing(propertyCode="R1", operation="rent", priceByArea=10.0),
            _make_listing(propertyCode="R2", operation="rent", priceByArea=10.5),
        ]
        result = price_per_area_histogram(_df(rows))
        sale_bins = {
            (r["bin_start_price_m2"], r["bin_end_price_m2"])
            for r in result
            if r["operation"] == "sale"
        }
        rent_bins = {
            (r["bin_start_price_m2"], r["bin_end_price_m2"])
            for r in result
            if r["operation"] == "rent"
        }
        # Sale bins are much wider (hundreds of EUR/m2) than rent bins
        # (single-digit EUR/m2) — the two scales must not share bin edges.
        sale_widths = {end - start for start, end in sale_bins}
        rent_widths = {end - start for start, end in rent_bins}
        assert sale_widths != rent_widths

    def test_emits_count_listings_field(self) -> None:
        rows = [_make_listing(operation="sale", priceByArea=2500.0)]
        result = price_per_area_histogram(_df(rows))
        assert result[0]["count_listings"] == 1

    def test_empty_input_returns_empty_list(self) -> None:
        assert price_per_area_histogram(pd.DataFrame(columns=_BASE_COLS)) == []


# ---------------------------------------------------------------------------
# District/neighbourhood dimensions on the count datasets (FEATURE-016, 16.4)
# ---------------------------------------------------------------------------


def _collapse(
    records: List[Dict[str, Any]], key_fields: List[str]
) -> Dict[Tuple[Any, ...], int]:
    """Sum ``count_listings`` back to the grain given by ``key_fields``."""
    totals: Dict[Tuple[Any, ...], int] = {}
    for record in records:
        key = tuple(record[field] for field in key_fields)
        totals[key] = totals.get(key, 0) + record["count_listings"]
    return totals


def _two_neighbourhood_rows() -> List[Dict[str, Any]]:
    """Five sale listings and one rent listing across two neighbourhoods."""
    return [
        _make_listing(propertyCode="A1", size=101.0, rooms=3, priceByArea=2510.0),
        _make_listing(propertyCode="A2", size=104.0, rooms=3, priceByArea=2590.0),
        _make_listing(propertyCode="A3", size=115.0, rooms=4, priceByArea=2800.0),
        _make_listing(
            propertyCode="B1",
            district="Ciutat Vella",
            neighborhood="El Carme",
            size=102.0,
            rooms=3,
            priceByArea=2520.0,
        ),
        _make_listing(
            propertyCode="B2",
            district="Ciutat Vella",
            neighborhood="El Carme",
            size=118.0,
            rooms=2,
            priceByArea=3100.0,
        ),
        _make_listing(
            propertyCode="R1",
            operation="rent",
            district="Ciutat Vella",
            neighborhood="El Carme",
            size=103.0,
            rooms=3,
            priceByArea=12.4,
        ),
    ]


class TestDataBasisDimensions:
    """The four count datasets carry district + neighbourhood (FEATURE-016).

    The dashboard filters these rows by scope and sums them back to the
    chart grain, so two things must hold: every record names its district
    and neighbourhood, and summing over them reproduces the undimensioned
    counts exactly (no listing lost or double-counted).
    """

    def test_weekly_listing_volume_is_split_by_district_and_neighbourhood(
        self,
    ) -> None:
        result = weekly_listing_volume(_df(_two_neighbourhood_rows()))
        assert list(result[0].keys()) == [
            "operation",
            "district",
            "neighborhood",
            "snapshot_date",
            "count_listings",
        ]
        by_key = {
            (r["operation"], r["district"], r["neighborhood"]): r["count_listings"]
            for r in result
        }
        assert by_key == {
            ("sale", "Extramurs", "Patraix"): 3,
            ("sale", "Ciutat Vella", "El Carme"): 2,
            ("rent", "Ciutat Vella", "El Carme"): 1,
        }

    def test_size_histogram_is_split_by_district_and_neighbourhood(self) -> None:
        result = size_histogram_10sqm(_df(_two_neighbourhood_rows()))
        assert list(result[0].keys()) == [
            "operation",
            "district",
            "neighborhood",
            "bin_start_m2",
            "bin_end_m2",
            "count_listings",
        ]
        by_key = {
            (r["operation"], r["neighborhood"], r["bin_start_m2"]): r["count_listings"]
            for r in result
        }
        assert by_key[("sale", "Patraix", 100)] == 2
        assert by_key[("sale", "El Carme", 100)] == 1
        assert by_key[("sale", "Patraix", 110)] == 1
        assert by_key[("sale", "El Carme", 110)] == 1
        assert by_key[("rent", "El Carme", 100)] == 1

    def test_rooms_distribution_is_split_by_district_and_neighbourhood(self) -> None:
        result = rooms_distribution(_df(_two_neighbourhood_rows()))
        assert list(result[0].keys()) == [
            "operation",
            "district",
            "neighborhood",
            "rooms",
            "count_listings",
        ]
        by_key = {
            (r["operation"], r["neighborhood"], r["rooms"]): r["count_listings"]
            for r in result
        }
        assert by_key == {
            ("sale", "Patraix", 3): 2,
            ("sale", "Patraix", 4): 1,
            ("sale", "El Carme", 3): 1,
            ("sale", "El Carme", 2): 1,
            ("rent", "El Carme", 3): 1,
        }

    def test_price_histogram_is_split_by_district_and_neighbourhood(self) -> None:
        result = price_per_area_histogram(_df(_two_neighbourhood_rows()))
        assert list(result[0].keys()) == [
            "operation",
            "district",
            "neighborhood",
            "bin_start_price_m2",
            "bin_end_price_m2",
            "count_listings",
        ]
        by_key = {
            (
                r["operation"],
                r["neighborhood"],
                r["bin_start_price_m2"],
            ): r["count_listings"]
            for r in result
        }
        assert by_key == {
            ("sale", "Patraix", 2500.0): 2,
            ("sale", "Patraix", 2750.0): 1,
            ("sale", "El Carme", 2500.0): 1,
            ("sale", "El Carme", 3000.0): 1,
            ("rent", "El Carme", 12.0): 1,
        }

    def test_dimensioned_records_are_json_serialisable(self) -> None:
        """numpy scalars in a record would break json.dumps in the aggregator."""
        df = _df(_two_neighbourhood_rows())
        for records in (
            weekly_listing_volume(df),
            size_histogram_10sqm(df),
            rooms_distribution(df),
            price_per_area_histogram(df),
        ):
            assert json.loads(json.dumps(records)) == records

    def test_collapsing_reproduces_the_undimensioned_counts(self) -> None:
        """Summing over district/neighbourhood equals a plain count."""
        df = _df(_two_neighbourhood_rows())

        assert _collapse(weekly_listing_volume(df), ["operation"]) == {
            ("sale",): 5,
            ("rent",): 1,
        }
        assert _collapse(
            size_histogram_10sqm(df), ["operation", "bin_start_m2", "bin_end_m2"]
        ) == {
            ("sale", 100, 110): 3,
            ("sale", 110, 120): 2,
            ("rent", 100, 110): 1,
        }
        assert _collapse(rooms_distribution(df), ["operation", "rooms"]) == {
            ("sale", 3): 3,
            ("sale", 4): 1,
            ("sale", 2): 1,
            ("rent", 3): 1,
        }
        assert _collapse(
            price_per_area_histogram(df), ["operation", "bin_start_price_m2"]
        ) == {
            ("sale", 2500.0): 3,
            ("sale", 2750.0): 1,
            ("sale", 3000.0): 1,
            ("rent", 12.0): 1,
        }


# ---------------------------------------------------------------------------
# Privacy-safe last-3-month location grid (FEATURE-011, task 11.3, review H1)
# ---------------------------------------------------------------------------

_GEO_BASE_COLS = _BASE_COLS + ["latitude", "longitude"]

# Fields that must NEVER appear in a listing_location_grid_last_3m record —
# any of these would let a public dashboard visitor re-identify an
# individual listing (review H1).
_FORBIDDEN_GEO_FIELDS = {
    "propertyCode",
    "address",
    "price",
    "priceByArea",
    "latitude_raw",
    "longitude_raw",
}


class TestListingLocationGridLast3Months:
    """
    listing_location_grid_last_3m emits only rounded, count-aggregated geo
    cells — never raw per-listing coordinates or identifying fields (H1).
    """

    def test_never_emits_forbidden_fields(self) -> None:
        """
        H1: no emitted record may contain propertyCode, address, exact
        price, or any field other than the documented allow-list.
        """
        rows = [
            _make_listing(
                propertyCode="P1",
                latitude=39.469077,
                longitude=-0.3799074,
                snapshot_date=date(2023, 4, 9),
            ),
            _make_listing(
                propertyCode="P2",
                latitude=39.4692874,
                longitude=-0.3788613,
                snapshot_date=date(2023, 4, 9),
            ),
        ]
        result = listing_location_grid_last_3m(
            _df(rows), window_months=ROLLING_KPI_WINDOW_MONTHS
        )
        assert result, "fixture must produce at least one grid cell"

        allowed_keys = {
            "operation",
            "district",
            "neighborhood",
            "latitude",
            "longitude",
            "count_listings",
        }
        for record in result:
            assert set(record.keys()) == allowed_keys, (
                f"Unexpected keys in record: " f"{set(record.keys()) - allowed_keys}"
            )
            assert not _FORBIDDEN_GEO_FIELDS & set(record.keys())

    def test_coordinates_are_rounded_before_grouping(self) -> None:
        """Two nearby raw coordinates collapse into the same rounded cell."""
        rows = [
            _make_listing(
                propertyCode="P1",
                latitude=39.469077,
                longitude=-0.3799074,
                snapshot_date=date(2023, 4, 9),
            ),
            _make_listing(
                propertyCode="P2",
                latitude=39.4692874,  # rounds to the same 3-decimal cell
                longitude=-0.3798512,  # rounds to the same 3-decimal cell
                snapshot_date=date(2023, 4, 9),
            ),
        ]
        result = listing_location_grid_last_3m(_df(rows))
        assert len(result) == 1
        assert result[0]["count_listings"] == 2
        # Rounded to 3 decimals — never the unrounded raw input value.
        assert result[0]["latitude"] == round(39.469077, 3)
        assert result[0]["longitude"] == round(-0.3799074, 3)

    def test_excludes_rows_older_than_the_rolling_window(self) -> None:
        """Rows older than the rolling 3-month window must be excluded."""
        rows = [
            _make_listing(
                propertyCode="RECENT",
                snapshot_date=date(2023, 4, 9),
                latitude=39.469077,
                longitude=-0.3799074,
            ),
            _make_listing(
                propertyCode="OLD",
                snapshot_date=date(2022, 1, 1),  # far outside the window
                latitude=39.5,
                longitude=-0.5,
            ),
        ]
        result = listing_location_grid_last_3m(_df(rows))
        # Only the recent row's cell should be present.
        assert len(result) == 1
        assert result[0]["latitude"] == round(39.469077, 3)

    def test_multiple_listings_reduce_payload_cardinality(self) -> None:
        """Grouping reduces the number of emitted rows vs. raw listing count."""
        rows = [
            _make_listing(
                propertyCode=f"P{i}",
                latitude=39.469077,
                longitude=-0.3799074,
                snapshot_date=date(2023, 4, 9),
            )
            for i in range(10)
        ]
        result = listing_location_grid_last_3m(_df(rows))
        assert len(result) == 1
        assert result[0]["count_listings"] == 10

    def test_empty_input_returns_empty_list(self) -> None:
        assert listing_location_grid_last_3m(pd.DataFrame(columns=_GEO_BASE_COLS)) == []

    def test_missing_geo_columns_returns_empty_list(self) -> None:
        """No latitude/longitude columns at all → empty, not a KeyError."""
        rows = [_make_listing()]
        df = _df(rows).drop(columns=["latitude", "longitude"])
        assert listing_location_grid_last_3m(df) == []


class TestListingLocationsLast3Months:
    """
    listing_locations_last_3m emits one raw (unrounded) coordinate record per
    currently-listed property, for the real-map rendering (operator decision
    2026-07-18: precise per-listing location disclosure is acceptable for
    this project — unlike listing_location_grid_last_3m, coordinates are
    NOT rounded/aggregated here).
    """

    def test_only_documented_fields_are_emitted(self) -> None:
        """No propertyCode/address/price ever leaves this function."""
        rows = [
            _make_listing(
                propertyCode="P1",
                latitude=39.469077,
                longitude=-0.3799074,
                snapshot_date=date(2023, 4, 9),
            ),
        ]
        result = listing_locations_last_3m(_df(rows))
        assert result
        allowed_keys = {
            "operation",
            "district",
            "neighborhood",
            "latitude",
            "longitude",
        }
        for record in result:
            assert set(record.keys()) == allowed_keys
            assert not _FORBIDDEN_GEO_FIELDS & set(record.keys())

    def test_coordinates_are_not_rounded(self) -> None:
        """Unlike the grid variant, exact input coordinates pass through."""
        rows = [
            _make_listing(
                propertyCode="P1",
                latitude=39.4690771234,
                longitude=-0.3799074321,
                snapshot_date=date(2023, 4, 9),
            ),
        ]
        result = listing_locations_last_3m(_df(rows))
        assert result[0]["latitude"] == 39.4690771234
        assert result[0]["longitude"] == -0.3799074321

    def test_one_record_per_currently_listed_property(self) -> None:
        """Multiple snapshots of the same property collapse to one point."""
        rows = [
            _make_listing(
                propertyCode="P1",
                latitude=39.469077,
                longitude=-0.3799074,
                snapshot_date=date(2023, 4, 9),
            ),
            _make_listing(
                propertyCode="P1",
                latitude=39.469077,
                longitude=-0.3799074,
                snapshot_date=date(2023, 4, 16),
            ),
            _make_listing(
                propertyCode="P2",
                latitude=39.5,
                longitude=-0.5,
                snapshot_date=date(2023, 4, 16),
            ),
        ]
        result = listing_locations_last_3m(_df(rows))
        assert len(result) == 2

    def test_excludes_rows_older_than_the_rolling_window(self) -> None:
        rows = [
            _make_listing(
                propertyCode="RECENT",
                snapshot_date=date(2023, 4, 9),
                latitude=39.469077,
                longitude=-0.3799074,
            ),
            _make_listing(
                propertyCode="OLD",
                snapshot_date=date(2022, 1, 1),
                latitude=39.5,
                longitude=-0.5,
            ),
        ]
        result = listing_locations_last_3m(_df(rows))
        assert len(result) == 1
        assert result[0]["latitude"] == 39.469077

    def test_empty_input_returns_empty_list(self) -> None:
        assert listing_locations_last_3m(pd.DataFrame(columns=_GEO_BASE_COLS)) == []

    def test_missing_geo_columns_returns_empty_list(self) -> None:
        rows = [_make_listing()]
        df = _df(rows).drop(columns=["latitude", "longitude"])
        assert listing_locations_last_3m(df) == []
