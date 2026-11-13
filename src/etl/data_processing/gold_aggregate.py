"""
Gold-layer aggregation for Idealista silver listings.

This module is intentionally pure (no AWS dependencies) so every function can
be unit-tested in isolation. The Lambda handler (gold_aggregation_lambda.py)
is responsible for reading silver Parquet, calling :func:`build_aggregation_json`,
and writing the result to S3.

The output JSON contract is frozen at schema v1.0. FEATURE-005 (static
visualization web app) depends on this exact shape — do not change field names
or structure without a schema-version bump.

Schema v1.0 top-level keys
---------------------------
- ``schema_version``  : "1.0"
- ``generated_at``    : ISO-8601 UTC timestamp
- ``scope_districts`` : ["Extramurs", "Ciutat Vella", "L'Eixample"]
- ``min_count``       : int, default 5; ratio pairs below this are excluded
- ``relevant_filter`` : dict echoing the relevant-population predicate
- ``general``         : population block — all scoped listings
- ``relevant``        : population block — "apartments like ours" filter

Population block keys
---------------------
general:
  - price_time_series_neighborhood
  - price_time_series_district   (count-weighted, not mean-of-means)
  - rent_vs_sale_ratio
  - rent_vs_sale_ratio_time_series
  - boxplot_by_neighborhood

relevant:
  - rent_vs_sale_ratio
  - rent_vs_sale_ratio_time_series
  - boxplot_by_neighborhood

Key design decisions (see REVIEW-FEATURE-004.md for rationale)
---------------------------------------------------------------
- Dedup only within (operation, snapshot_date, propertyCode) so the same
  property appearing in multiple snapshots contributes to each snapshot's
  time-series point — global dedup would collapse history.
- District-level price series uses count-weighted mean:
  ``sum(count * mean) / sum(count)`` — NOT mean-of-means.
- Boxplot ships a 5-number summary (min/q1/median/q3/max) + count over the
  full history. Plotly renders box traces from quartiles.
- ``floor`` is a string in the silver data; comparisons use ``!= "1"``.
- Output key is ``mean_price`` (never ``mean_prize``).
- ``min_count`` applies to ratio datasets: neighborhoods where either the
  sale or rent side has fewer than ``min_count`` listings are excluded.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, cast

import pandas as pd

from common.search_config import IDEALISTA_SEARCH_PARAMS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCOPE_DISTRICTS: List[str] = ["Extramurs", "Ciutat Vella", "L'Eixample"]

_DEFAULT_MIN_COUNT: int = 5

# Single named constant for the rolling KPI window length (FEATURE-010).
# The rolling window is relative to max(snapshot_date) in the scoped/deduped
# data, not wall-clock now — see _rolling_window_start().
ROLLING_KPI_WINDOW_MONTHS: int = 3

# The "apartments like ours" predicate, echoed into the JSON contract.
RELEVANT_FILTER_SPEC: Dict[str, Any] = {
    "hasLift": True,
    "floor_not": "1",
    "size_gt": 120,
    "rooms_gte": 2,
    "bathrooms_gte": 2,
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """
    Return the current UTC time as an ISO-8601 string.

    Single source of the ``generated_at`` timestamp for both
    :func:`build_aggregation_json` and ``gold_aggregator.GoldAggregator``,
    so the golden-master test can freeze time by patching this module's
    ``datetime`` reference once.
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _rolling_window_start(
    df: pd.DataFrame, window_months: int = ROLLING_KPI_WINDOW_MONTHS
) -> Optional[pd.Timestamp]:
    """
    Compute the inclusive start of a rolling calendar-month window.

    The window is anchored to ``max(snapshot_date)`` within ``df`` — never to
    wall-clock "now" — so backfills and delayed collection runs remain
    deterministic. ``snapshot_date`` values are normalised with
    ``pd.to_datetime(..., errors="coerce")`` so ISO strings, ``datetime.date``,
    and pandas ``Timestamp`` inputs all produce the same window start (M2).

    Args:
        df: DataFrame with a ``snapshot_date`` column (any date-like dtype).
        window_months: Number of calendar months to look back. Defaults to
            :data:`ROLLING_KPI_WINDOW_MONTHS`.

    Returns:
        The inclusive window start as a ``pd.Timestamp``, or ``None`` when
        ``df`` is empty or contains no parseable ``snapshot_date`` values.
    """
    if df.empty or "snapshot_date" not in df.columns:
        return None

    parsed = pd.to_datetime(df["snapshot_date"], errors="coerce")
    parsed = parsed.dropna()
    if parsed.empty:
        return None

    max_date = parsed.max()
    return cast(pd.Timestamp, max_date - pd.DateOffset(months=window_months))


def _relevant_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Relevant-population predicate: apartments matching our search.

    Mirrors :data:`RELEVANT_FILTER_SPEC` exactly — shared by the pure
    entry point and the strategy-based ``GoldAggregator``.
    """
    return df[
        (df["hasLift"] == True)  # noqa: E712
        & (df["floor"] != "1")
        & (df["size"] > 120)
        & (df["rooms"] >= 2)
        & (df["bathrooms"] >= 2)
    ]


def apply_scope(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter a silver DataFrame to the 3 scope districts.

    The scope covers Valencia's three target districts:
    Extramurs, Ciutat Vella, and L\u2019Eixample. Rows from any other district are
    dropped. The district string ``"L\u2019Eixample"`` includes a typographic
    apostrophe (U+2019) — tests assert this exact byte.

    Args:
        df: Silver listings DataFrame with at least a ``district`` column.

    Returns:
        A filtered copy containing only rows whose ``district`` value is one of
        the 3 scope districts. Returns an empty DataFrame (same columns) when
        the input is empty or all rows are out of scope.
    """
    if df.empty:
        return df.copy()
    return df[df["district"].isin(SCOPE_DISTRICTS)].copy()


def _dedup(df: pd.DataFrame) -> pd.DataFrame:
    """
    Deduplicate within (operation, snapshot_date, propertyCode).

    CRITICAL: dedup must be scoped to a single snapshot. Dropping duplicates
    globally on propertyCode would collapse the time-series (a property
    re-listed in week 2 would lose its week-2 data point).

    Args:
        df: Scoped silver DataFrame.

    Returns:
        DataFrame with at most one row per (operation, snapshot_date,
        propertyCode) triple. Row order is not guaranteed.
    """
    if df.empty:
        return df.copy()
    return df.drop_duplicates(
        subset=["operation", "snapshot_date", "propertyCode"]
    ).copy()


def _price_time_series_neighborhood(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Build price time-series aggregated at neighborhood grain.

    Groups by (operation, district, neighborhood, snapshot_date) and computes:
    - ``count_listings``  : number of listings
    - ``mean_priceByArea``: average price per m2
    - ``mean_size``       : average size in m2
    - ``mean_price``      : average total price (key is ``mean_price``, never ``mean_prize``)

    Args:
        df: Deduped, scoped listings DataFrame.

    Returns:
        List of record dicts. Empty list when input is empty.
    """
    if df.empty:
        return []

    grp = (
        df.groupby(["operation", "district", "neighborhood", "snapshot_date"])
        .agg(
            count_listings=("propertyCode", "count"),
            mean_priceByArea=("priceByArea", "mean"),
            mean_size=("size", "mean"),
            mean_price=("price", "mean"),
        )
        .reset_index()
    )

    # Convert snapshot_date to a serialisable string for the JSON contract.
    grp["snapshot_date"] = grp["snapshot_date"].apply(
        lambda v: v.isoformat() if isinstance(v, date) else str(v)
    )

    # Column labels are strings; cast narrows pandas' Hashable keys for mypy.
    return cast(List[Dict[str, Any]], grp.to_dict(orient="records"))


def _price_time_series_district(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Build price time-series aggregated at district grain using count-weighting.

    A simple mean-of-means would over-represent small neighborhoods. Instead
    the district mean is computed as::

        sum(count_in_neighborhood * mean_priceByArea_in_neighborhood)
        ─────────────────────────────────────────────────────────────
                   sum(count_in_neighborhood)

    This matches the notebook's approach (aggregating all listings together,
    not aggregating pre-computed neighborhood means).

    Args:
        df: Deduped, scoped listings DataFrame.

    Returns:
        List of record dicts with keys: operation, district, snapshot_date,
        count_listings, mean_priceByArea, mean_size, mean_price.
        Empty list when input is empty.
    """
    if df.empty:
        return []

    # Use the raw listings for count-weighted aggregation (equivalent to
    # grouping all listings directly at district grain).
    grp = (
        df.groupby(["operation", "district", "snapshot_date"])
        .agg(
            count_listings=("propertyCode", "count"),
            mean_priceByArea=("priceByArea", "mean"),
            mean_size=("size", "mean"),
            mean_price=("price", "mean"),
        )
        .reset_index()
    )

    grp["snapshot_date"] = grp["snapshot_date"].apply(
        lambda v: v.isoformat() if isinstance(v, date) else str(v)
    )

    # Column labels are strings; cast narrows pandas' Hashable keys for mypy.
    return cast(List[Dict[str, Any]], grp.to_dict(orient="records"))


def _rent_vs_sale_ratio(
    df: pd.DataFrame,
    min_count: int = _DEFAULT_MIN_COUNT,
) -> List[Dict[str, Any]]:
    """
    Compute the full-history rent-vs-sale price ratio per (district, neighborhood).

    Aggregates all snapshots together (full-history view) then pivots on
    operation. Pairs where either side has fewer than ``min_count`` listings
    are excluded to avoid misleading ratios from sparse data.

    Args:
        df: Deduped, scoped listings DataFrame.
        min_count: Minimum listing count on each side of the ratio before the
            neighborhood-pair is included in the output. Default 5.

    Returns:
        List of record dicts. Keys: district, neighborhood,
        mean_priceByArea_sale, mean_priceByArea_rent,
        mean_sales_price_by_rent_ratio, count_listings_sale,
        count_listings_rent. Empty list when no qualifying pair exists.
    """
    if df.empty:
        return []

    agg = (
        df.groupby(["operation", "district", "neighborhood"])
        .agg(
            count_listings=("propertyCode", "count"),
            mean_priceByArea=("priceByArea", "mean"),
        )
        .reset_index()
    )

    sale = agg[agg["operation"] == "sale"][
        ["district", "neighborhood", "count_listings", "mean_priceByArea"]
    ].rename(
        columns={
            "count_listings": "count_listings_sale",
            "mean_priceByArea": "mean_priceByArea_sale",
        }
    )
    rent = agg[agg["operation"] == "rent"][
        ["district", "neighborhood", "count_listings", "mean_priceByArea"]
    ].rename(
        columns={
            "count_listings": "count_listings_rent",
            "mean_priceByArea": "mean_priceByArea_rent",
        }
    )

    merged = sale.merge(rent, on=["district", "neighborhood"], how="inner")

    # Apply min_count filter on both sides.
    merged = merged[
        (merged["count_listings_sale"] >= min_count)
        & (merged["count_listings_rent"] >= min_count)
    ]

    if merged.empty:
        return []

    merged["mean_sales_price_by_rent_ratio"] = (
        merged["mean_priceByArea_sale"] / merged["mean_priceByArea_rent"]
    )

    return cast(
        List[Dict[str, Any]],
        merged[
            [
                "district",
                "neighborhood",
                "mean_priceByArea_sale",
                "mean_priceByArea_rent",
                "mean_sales_price_by_rent_ratio",
                "count_listings_sale",
                "count_listings_rent",
            ]
        ].to_dict(orient="records"),
    )


def _rent_vs_sale_ratio_time_series(
    df: pd.DataFrame,
    min_count: int = _DEFAULT_MIN_COUNT,
) -> List[Dict[str, Any]]:
    """
    Compute the rent-vs-sale price ratio per (district, neighborhood, snapshot_date).

    Same logic as :func:`_rent_vs_sale_ratio` but computed independently for
    each ``snapshot_date``, so callers can plot the ratio as a time-series line
    chart.

    Args:
        df: Deduped, scoped listings DataFrame.
        min_count: Minimum count on each side per snapshot. Default 5.

    Returns:
        List of record dicts. Keys: district, neighborhood, snapshot_date,
        mean_priceByArea_sale, mean_priceByArea_rent,
        mean_sales_price_by_rent_ratio, count_listings_sale,
        count_listings_rent. Empty list when no qualifying snapshot-pair exists.
    """
    if df.empty:
        return []

    agg = (
        df.groupby(["operation", "district", "neighborhood", "snapshot_date"])
        .agg(
            count_listings=("propertyCode", "count"),
            mean_priceByArea=("priceByArea", "mean"),
        )
        .reset_index()
    )

    sale = agg[agg["operation"] == "sale"][
        [
            "district",
            "neighborhood",
            "snapshot_date",
            "count_listings",
            "mean_priceByArea",
        ]
    ].rename(
        columns={
            "count_listings": "count_listings_sale",
            "mean_priceByArea": "mean_priceByArea_sale",
        }
    )
    rent = agg[agg["operation"] == "rent"][
        [
            "district",
            "neighborhood",
            "snapshot_date",
            "count_listings",
            "mean_priceByArea",
        ]
    ].rename(
        columns={
            "count_listings": "count_listings_rent",
            "mean_priceByArea": "mean_priceByArea_rent",
        }
    )

    merged = sale.merge(
        rent, on=["district", "neighborhood", "snapshot_date"], how="inner"
    )
    merged = merged[
        (merged["count_listings_sale"] >= min_count)
        & (merged["count_listings_rent"] >= min_count)
    ]

    if merged.empty:
        return []

    merged["mean_sales_price_by_rent_ratio"] = (
        merged["mean_priceByArea_sale"] / merged["mean_priceByArea_rent"]
    )

    merged["snapshot_date"] = merged["snapshot_date"].apply(
        lambda v: v.isoformat() if isinstance(v, date) else str(v)
    )

    return cast(
        List[Dict[str, Any]],
        merged[
            [
                "district",
                "neighborhood",
                "snapshot_date",
                "mean_priceByArea_sale",
                "mean_priceByArea_rent",
                "mean_sales_price_by_rent_ratio",
                "count_listings_sale",
                "count_listings_rent",
            ]
        ].to_dict(orient="records"),
    )


def _boxplot_summary(df: pd.DataFrame, min_count: int = 1) -> List[Dict[str, Any]]:
    """
    Shared core: 5-number ``priceByArea`` summary per (operation, district,
    neighborhood) group, with an optional minimum-count stability guard.

    Both the all-time :func:`_boxplot_by_neighborhood` and the rolling
    :func:`_boxplot_by_neighborhood_last_months` delegate to this single
    implementation so the quantile/groupby math is never duplicated (M3).

    Args:
        df: Deduped, scoped (and, for the windowed caller, already
            date-filtered) listings DataFrame.
        min_count: Minimum number of listings a group must have (after
            dropping NaN prices) to be included. Default 1 (no filtering) —
            the all-time boxplot passes through every non-empty group;
            callers that need stability filtering pass a higher threshold.

    Returns:
        List of record dicts. Keys: operation, district, neighborhood, count,
        min, q1, median, q3, max. Empty list when input is empty.
    """
    if df.empty:
        return []

    records: List[Dict[str, Any]] = []
    for (op, dist, nbhd), group in df.groupby(
        ["operation", "district", "neighborhood"]
    ):
        prices = group["priceByArea"].dropna()
        if prices.empty or len(prices) < min_count:
            continue
        records.append(
            {
                "operation": op,
                "district": dist,
                "neighborhood": nbhd,
                "count": int(len(prices)),
                "min": float(prices.min()),
                "q1": float(prices.quantile(0.25)),
                "median": float(prices.quantile(0.50)),
                "q3": float(prices.quantile(0.75)),
                "max": float(prices.max()),
            }
        )

    return records


def _boxplot_by_neighborhood(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Compute a 5-number summary of priceByArea per (operation, district, neighborhood).

    The summary is computed over the full history (all snapshots combined).
    Plotly box traces can render directly from quartile values, so raw rows
    are never shipped. This keeps latest.json small and the chart fast.

    This all-time dataset is intentionally NOT filtered by ``min_count`` —
    every group with at least one listing is included, preserving the
    original schema-v1.0 semantics.

    Args:
        df: Deduped, scoped listings DataFrame.

    Returns:
        List of record dicts. Keys: operation, district, neighborhood, count,
        min, q1, median, q3, max. Empty list when input is empty.
    """
    return _boxplot_summary(df, min_count=1)


def _boxplot_by_neighborhood_last_months(
    df: pd.DataFrame,
    window_months: int = ROLLING_KPI_WINDOW_MONTHS,
    min_count: int = _DEFAULT_MIN_COUNT,
) -> List[Dict[str, Any]]:
    """
    Compute the rolling-window 5-number priceByArea summary per neighborhood.

    Filters to rows with ``snapshot_date >= window_start`` (inclusive), where
    ``window_start`` is anchored to ``max(snapshot_date)`` in ``df`` — never
    wall-clock now (see :func:`_rolling_window_start`) — then delegates to the
    shared :func:`_boxplot_summary` core so quantile math is not duplicated
    (M3). The existing ``min_count`` stability guard is applied inside the
    window so sparse recent groups are excluded (H2).

    Args:
        df: Deduped, scoped listings DataFrame.
        window_months: Rolling window length in calendar months. Defaults to
            :data:`ROLLING_KPI_WINDOW_MONTHS`.
        min_count: Minimum listings a group must have inside the window to be
            included. Default 5 (:data:`_DEFAULT_MIN_COUNT`).

    Returns:
        List of record dicts with the same keys/shape as
        :func:`_boxplot_by_neighborhood`. Empty list when there is no data,
        no parseable ``snapshot_date``, or no group meets ``min_count``.
    """
    if df.empty:
        return []

    window_start = _rolling_window_start(df, window_months)
    if window_start is None:
        return []

    parsed_dates = pd.to_datetime(df["snapshot_date"], errors="coerce")
    windowed = df[parsed_dates >= window_start]

    return _boxplot_summary(windowed, min_count=min_count)


# ---------------------------------------------------------------------------
# Data Basis pure aggregation helpers (FEATURE-011)
# ---------------------------------------------------------------------------
#
# These helpers operate on UNSCOPED Silver data (all districts, not just the
# 3 scope districts used by general/relevant) and must never alter
# apply_scope(), _dedup(), or any general/relevant behaviour (review H2).
# They feed the additive top-level `data_basis` block wired in
# gold_aggregator.py (task 11.4).

# Operation-specific bin widths for the price-per-m2 histogram: sale and
# rent prices live on very different scales (hundreds vs. single-digit
# EUR/m2), so a single bin width would make one side unreadable.
_PRICE_PER_AREA_BIN_WIDTH: Dict[str, float] = {"sale": 250.0, "rent": 1.0}
_DEFAULT_PRICE_PER_AREA_BIN_WIDTH: float = 250.0

# Bin width for the listing-size histogram (m2).
_SIZE_HISTOGRAM_BIN_WIDTH_M2: int = 10

# Coordinate rounding precision for the privacy-safe location grid: 3 decimal
# degrees is roughly 80-110 m in Valencia's latitude — coarse enough that no
# individual listing's exact location is exposed (review H1).
_LOCATION_GRID_PRECISION_DECIMALS: int = 3

# Location dimensions carried by every Data Basis count dataset
# (FEATURE-016). The dashboard filters rows on these two fields and then
# sums count_listings back to the chart grain, so they are group-by keys in
# addition to each dataset's own grain. Silver drops rows without a
# neighbourhood, so grouping on them never loses a listing.
_LOCATION_DIMENSIONS: List[str] = ["district", "neighborhood"]


def search_config_summary() -> Dict[str, Any]:
    """
    Serialize the shared Idealista search constants for the public dashboard.

    Reads exclusively from :data:`common.search_config.IDEALISTA_SEARCH_PARAMS`
    (single source of truth shared with :class:`bronze_collector.SearchConfig`,
    review M2) and re-shapes it into a small, stable public schema — the
    dashboard never sees collector-internal fields (``base_url``, ``order``,
    ``sort``, ``max_items``, ``language``, ``country``) that could change for
    reasons unrelated to the search itself.

    Returns:
        Dict with keys: center_lat, center_lon, distance_m, min_size_m2,
        max_size_m2, elevator, air_conditioning, preservation, property_type,
        sale_credential_label, rent_credential_label.
    """
    params = IDEALISTA_SEARCH_PARAMS
    return {
        "center_lat": params["center_lat"],
        "center_lon": params["center_lon"],
        "distance_m": params["distance_m"],
        "min_size_m2": params["min_size_m2"],
        "max_size_m2": params["max_size_m2"],
        "elevator": params["elevator"],
        "air_conditioning": params["air_conditioning"],
        "preservation": params["preservation"],
        "property_type": params["property_type"],
        "sale_credential_label": params["sale_credential_label"],
        "rent_credential_label": params["rent_credential_label"],
    }


def weekly_listing_volume(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Count listings per (operation, district, neighborhood, snapshot_date).

    Callers must pass rows already deduped within each snapshot (e.g. via
    :func:`_dedup`) — this helper does not dedup itself, matching the
    per-snapshot dedup semantics used everywhere else in the gold layer.
    The helper applies no scope filter of its own; ``GoldAggregator`` passes
    it rows already restricted to the scope districts.

    Args:
        df: Per-snapshot-deduped listings DataFrame (any scope).

    Returns:
        List of record dicts: operation, district, neighborhood,
        snapshot_date, count_listings. Summing count_listings over district
        and neighborhood yields the count per (operation, snapshot_date).
        Empty list when input is empty.
    """
    if df.empty:
        return []

    grp = (
        df.groupby(["operation", *_LOCATION_DIMENSIONS, "snapshot_date"])
        .agg(count_listings=("propertyCode", "count"))
        .reset_index()
    )
    grp["snapshot_date"] = grp["snapshot_date"].apply(
        lambda v: v.isoformat() if isinstance(v, date) else str(v)
    )
    return cast(List[Dict[str, Any]], grp.to_dict(orient="records"))


def latest_by_property(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep only the most recent snapshot row per (operation, propertyCode).

    Used by the Data Basis distribution datasets (size, rooms, price/m2),
    which describe "what's currently listed" rather than full history —
    unlike the per-snapshot dedup used for time-series datasets.

    Args:
        df: Listings DataFrame (any scope), with a ``snapshot_date`` column.

    Returns:
        DataFrame with at most one row per (operation, propertyCode),
        keeping the row with the latest parsed ``snapshot_date``. Returns
        an empty copy (same columns) when the input is empty.
    """
    if df.empty:
        return df.copy()

    working = df.copy()
    working["_parsed_snapshot"] = pd.to_datetime(
        working["snapshot_date"], errors="coerce"
    )
    working = working.sort_values("_parsed_snapshot", kind="stable")
    deduped = working.drop_duplicates(subset=["operation", "propertyCode"], keep="last")
    return deduped.drop(columns=["_parsed_snapshot"])


def listing_location_grid_last_3m(
    df: pd.DataFrame,
    window_months: int = ROLLING_KPI_WINDOW_MONTHS,
    precision_decimals: int = _LOCATION_GRID_PRECISION_DECIMALS,
) -> List[Dict[str, Any]]:
    """
    Privacy-safe, rolling-window aggregate of listing locations for the map.

    **Never emits raw per-listing coordinates.** Coordinates are rounded to
    ``precision_decimals`` (default 3 — roughly 80-110 m in Valencia) BEFORE
    grouping, and only the aggregate ``count_listings`` is emitted alongside
    ``operation``/``district``/``neighborhood``/rounded ``latitude``/
    ``longitude``. No ``propertyCode``, address, exact price, or unrounded
    coordinate ever leaves this function (review H1).

    Reuses :func:`_rolling_window_start` (the FEATURE-010 rolling-window
    helper) for the last-3-month filter and :func:`latest_by_property` for
    the latest-snapshot-per-property dedup, so this windowing/dedup logic is
    never duplicated.

    Args:
        df: Listings DataFrame (any scope — Data Basis is unscoped), with
            ``latitude``/``longitude`` columns.
        window_months: Rolling window length in calendar months. Defaults to
            :data:`ROLLING_KPI_WINDOW_MONTHS`.
        precision_decimals: Coordinate rounding precision in decimal degrees.
            Defaults to :data:`_LOCATION_GRID_PRECISION_DECIMALS`.

    Returns:
        List of record dicts with EXACTLY these keys: operation, district,
        neighborhood, latitude, longitude, count_listings. Empty list when
        input is empty, has no geo columns, has no parseable snapshot dates,
        or no rows fall inside the window.
    """
    if df.empty:
        return []
    if not {"latitude", "longitude"}.issubset(df.columns):
        return []

    window_start = _rolling_window_start(df, window_months)
    if window_start is None:
        return []

    parsed_dates = pd.to_datetime(df["snapshot_date"], errors="coerce")
    windowed = df[parsed_dates >= window_start]
    if windowed.empty:
        return []

    latest = latest_by_property(windowed)
    working = latest.dropna(subset=["latitude", "longitude"]).copy()
    if working.empty:
        return []

    working["latitude"] = working["latitude"].round(precision_decimals)
    working["longitude"] = working["longitude"].round(precision_decimals)

    grp = (
        working.groupby(
            ["operation", "district", "neighborhood", "latitude", "longitude"]
        )
        .size()
        .reset_index(name="count_listings")
        .sort_values(["operation", "district", "neighborhood", "latitude", "longitude"])
    )

    # Explicit column allow-list: guards against ever leaking any other
    # column, even if the input DataFrame carries extra fields (H1).
    return cast(
        List[Dict[str, Any]],
        grp[
            [
                "operation",
                "district",
                "neighborhood",
                "latitude",
                "longitude",
                "count_listings",
            ]
        ].to_dict(orient="records"),
    )


def listing_locations_last_3m(
    df: pd.DataFrame,
    window_months: int = ROLLING_KPI_WINDOW_MONTHS,
) -> List[Dict[str, Any]]:
    """
    Raw (unrounded, un-aggregated) per-listing coordinates for the last
    ``window_months`` months, one record per currently-listed property.

    Unlike :func:`listing_location_grid_last_3m`, this emits exact
    ``latitude``/``longitude`` per listing (operator decision: the map
    should render individual points on a real street map, precise location
    disclosure is acceptable for this project). Still excludes
    ``propertyCode`` and price so no per-listing financial/identity data
    leaks, and still reuses :func:`_rolling_window_start` and
    :func:`latest_by_property` so the windowing/dedup logic matches every
    other Data Basis dataset exactly.

    Args:
        df: Listings DataFrame (any scope — Data Basis is unscoped), with
            ``latitude``/``longitude`` columns.
        window_months: Rolling window length in calendar months. Defaults to
            :data:`ROLLING_KPI_WINDOW_MONTHS`.

    Returns:
        List of record dicts with EXACTLY these keys: operation, district,
        neighborhood, latitude, longitude. Empty list when input is empty,
        has no geo columns, has no parseable snapshot dates, or no rows
        fall inside the window.
    """
    if df.empty:
        return []
    if not {"latitude", "longitude"}.issubset(df.columns):
        return []

    window_start = _rolling_window_start(df, window_months)
    if window_start is None:
        return []

    parsed_dates = pd.to_datetime(df["snapshot_date"], errors="coerce")
    windowed = df[parsed_dates >= window_start]
    if windowed.empty:
        return []

    latest = latest_by_property(windowed)
    working = latest.dropna(subset=["latitude", "longitude"]).copy()
    if working.empty:
        return []

    working = working.sort_values(["operation", "district", "neighborhood"])

    return cast(
        List[Dict[str, Any]],
        working[
            [
                "operation",
                "district",
                "neighborhood",
                "latitude",
                "longitude",
            ]
        ].to_dict(orient="records"),
    )


def size_histogram_10sqm(
    df: pd.DataFrame, bin_width_m2: int = _SIZE_HISTOGRAM_BIN_WIDTH_M2
) -> List[Dict[str, Any]]:
    """
    Bin listings into deterministic 10 m2 size buckets, per operation,
    district and neighborhood.

    Bin edges are computed as ``floor(size / bin_width) * bin_width`` so the
    same size always falls into the same bin regardless of the surrounding
    data (deterministic bin edges).

    Args:
        df: Listings DataFrame (typically latest-by-property deduped).
        bin_width_m2: Bucket width in m2. Defaults to 10.

    Returns:
        List of record dicts: operation, district, neighborhood,
        bin_start_m2, bin_end_m2, count_listings. Empty list when input is
        empty or ``size`` is entirely missing.
    """
    if df.empty:
        return []

    working = df.dropna(subset=["size"])
    if working.empty:
        return []

    bin_start = (working["size"] // bin_width_m2 * bin_width_m2).astype(int)
    binned = pd.DataFrame(
        {
            "operation": working["operation"],
            "district": working["district"],
            "neighborhood": working["neighborhood"],
            "bin_start_m2": bin_start,
            "bin_end_m2": bin_start + bin_width_m2,
        }
    )
    grp = (
        binned.groupby(
            ["operation", *_LOCATION_DIMENSIONS, "bin_start_m2", "bin_end_m2"]
        )
        .size()
        .reset_index(name="count_listings")
        .sort_values(["operation", *_LOCATION_DIMENSIONS, "bin_start_m2"])
    )
    return cast(List[Dict[str, Any]], grp.to_dict(orient="records"))


def rooms_distribution(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Count listings per (operation, district, neighborhood, rooms).

    Args:
        df: Listings DataFrame (typically latest-by-property deduped).

    Returns:
        List of record dicts: operation, district, neighborhood, rooms,
        count_listings. Empty list when input is empty or ``rooms`` is
        entirely missing.
    """
    if df.empty:
        return []

    working = df.dropna(subset=["rooms"]).copy()
    if working.empty:
        return []

    working["rooms"] = working["rooms"].astype(int)
    grp = (
        working.groupby(["operation", *_LOCATION_DIMENSIONS, "rooms"])
        .size()
        .reset_index(name="count_listings")
        .sort_values(["operation", *_LOCATION_DIMENSIONS, "rooms"])
    )
    return cast(List[Dict[str, Any]], grp.to_dict(orient="records"))


def price_per_area_histogram(
    df: pd.DataFrame,
    bin_width_by_operation: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """
    Bin listings by priceByArea (EUR/m2) per operation, district and
    neighborhood, with operation-specific bin widths.

    Sale and rent prices per m2 live on very different scales, so each
    operation gets its own bin width (:data:`_PRICE_PER_AREA_BIN_WIDTH`) —
    sharing bin edges across operations would make one side unreadable.

    Args:
        df: Listings DataFrame (typically latest-by-property deduped).
        bin_width_by_operation: Optional override of the default per-operation
            bin widths (mainly for tests).

    Returns:
        List of record dicts: operation, district, neighborhood,
        bin_start_price_m2, bin_end_price_m2, count_listings. Empty list
        when input is empty or ``priceByArea`` is entirely missing.
    """
    if df.empty:
        return []

    working = df.dropna(subset=["priceByArea"])
    if working.empty:
        return []

    widths = bin_width_by_operation or _PRICE_PER_AREA_BIN_WIDTH

    records: List[Dict[str, Any]] = []
    for operation, group in working.groupby("operation"):
        width = widths.get(str(operation), _DEFAULT_PRICE_PER_AREA_BIN_WIDTH)
        bin_start = (group["priceByArea"] // width * width).astype(float)
        binned = pd.DataFrame(
            {
                "district": group["district"],
                "neighborhood": group["neighborhood"],
                "bin_start_price_m2": bin_start,
                "bin_end_price_m2": bin_start + width,
            }
        )
        counts = (
            binned.groupby(
                [*_LOCATION_DIMENSIONS, "bin_start_price_m2", "bin_end_price_m2"]
            )
            .size()
            .reset_index(name="count_listings")
            .sort_values([*_LOCATION_DIMENSIONS, "bin_start_price_m2"])
        )
        for row in counts.to_dict(orient="records"):
            records.append(
                {
                    "operation": operation,
                    "district": row["district"],
                    "neighborhood": row["neighborhood"],
                    "bin_start_price_m2": float(row["bin_start_price_m2"]),
                    "bin_end_price_m2": float(row["bin_end_price_m2"]),
                    "count_listings": int(row["count_listings"]),
                }
            )

    return records


# ---------------------------------------------------------------------------
# Generic population-block builder
# ---------------------------------------------------------------------------


def build_population_block(
    df: pd.DataFrame,
    row_filter: Optional[Callable[[pd.DataFrame], pd.DataFrame]],
    min_count: int = _DEFAULT_MIN_COUNT,
) -> Dict[str, Any]:
    """
    Build one population block for the gold aggregation JSON.

    A population block is the unit of symmetry in the schema: the general
    population uses an identity filter (``row_filter=None``) while the
    relevant population uses a predicate that selects "apartments like ours".
    Calling this function twice — once per filter — keeps the logic DRY.

    The general population emits 5 datasets; the relevant population emits 3
    (no price time-series, because the sub-population is too small to produce
    a stable neighborhood-level series).

    Args:
        df: Scoped, but not yet filtered or deduped, silver DataFrame.
        row_filter: Optional callable ``(df) -> df`` that restricts rows to the
            target sub-population. Pass ``None`` for the general (all-listings)
            population.
        min_count: Minimum listing count per side before a ratio pair is
            included. Forwarded to ratio helpers. Default 5.

    Returns:
        Dict with dataset keys appropriate for the chosen population.
        All dataset values are lists of record dicts (empty list when no data).
    """
    # Apply optional sub-population filter, then dedup within each snapshot.
    filtered = row_filter(df) if row_filter is not None else df
    deduped = _dedup(filtered)

    is_general = row_filter is None

    block: Dict[str, Any] = {}

    if is_general:
        block["price_time_series_neighborhood"] = _price_time_series_neighborhood(
            deduped
        )
        block["price_time_series_district"] = _price_time_series_district(deduped)

    block["rent_vs_sale_ratio"] = _rent_vs_sale_ratio(deduped, min_count=min_count)
    block["rent_vs_sale_ratio_time_series"] = _rent_vs_sale_ratio_time_series(
        deduped, min_count=min_count
    )
    block["boxplot_by_neighborhood"] = _boxplot_by_neighborhood(deduped)
    block["boxplot_by_neighborhood_last_3m"] = _boxplot_by_neighborhood_last_months(
        deduped, min_count=min_count
    )

    return block


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def build_aggregation_json(
    silver_df: pd.DataFrame,
    min_count: int = _DEFAULT_MIN_COUNT,
) -> Dict[str, Any]:
    """
    Build the frozen schema-v1.0 aggregation JSON from a full silver DataFrame.

    This is the pure entry point called by the Lambda handler. It scopes the
    data, builds two symmetric population blocks (general and relevant), and
    assembles the top-level contract.

    Args:
        silver_df: Combined silver DataFrame of cleaned individual listings
            across the full history (all operations, all snapshot_dates).
        min_count: Minimum listing count per side for ratio datasets. Defaults
            to 5; the Lambda handler reads this from the ``RATIO_MIN_COUNT``
            environment variable.

    Returns:
        A dict matching the frozen schema v1.0. All dataset fields are lists of
        record dicts. ``generated_at`` is the current UTC time in ISO-8601.
    """
    scoped = apply_scope(silver_df)

    general_block = build_population_block(scoped, row_filter=None, min_count=min_count)
    relevant_block = build_population_block(
        scoped, row_filter=_relevant_rows, min_count=min_count
    )

    return {
        "schema_version": "1.0",
        "generated_at": utc_now_iso(),
        "scope_districts": SCOPE_DISTRICTS,
        "min_count": min_count,
        "relevant_filter": RELEVANT_FILTER_SPEC,
        "general": general_block,
        "relevant": relevant_block,
    }
