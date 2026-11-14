# Gold Layer — Data Architecture & JSON Contract

## Overview

The gold layer transforms the silver cleaned-listings Parquet history into a
compact, dashboard-ready JSON file consumed by the FEATURE-005 static
visualization web app. It runs as a scheduled AWS Lambda (every Sunday at
12:45 UTC, 15 minutes after the silver cleaner) and writes a single file:

```
s3://<bucket>/gold/aggregations/latest.json
```

The output is **idempotent** — each run overwrites the same key — and covers
the **full time-series history** (all weeks since April 2023).

---

## Medallion Architecture Position

```
Bronze (raw API JSON)
        ↓  silver_cleaning_lambda   cron(30 12 ? * SUN *)
Silver (cleaned listings Parquet, partitioned by operation/snapshot_date)
        ↓  gold_aggregation_lambda  cron(45 12 ? * SUN *)
Gold   (pre-aggregated dashboard JSON — latest.json)
        ↓
FEATURE-005 (static visualization web app)
```

---

## Source Code

| File | Purpose |
|---|---|
| `src/etl/common/search_config.py` | Single source of truth for Idealista search parameters — shared by the collector and the gold `search_config` dataset (FEATURE-011) |
| `src/etl/data_processing/gold_aggregate.py` | Pure (AWS-free) aggregation helpers — analytical math, shared by both the legacy entry point and the strategies |
| `src/etl/data_processing/gold_aggregator.py` | `GoldAggregator` + `Aggregation` strategies — orchestration and dataset composition, including the additive `data_basis` strategies |
| `src/etl/data_processing/gold_aggregation_lambda.py` | Thin Lambda handler — Factory wire-up + response shaping |
| `src/etl/data_processing/tests/test_gold_aggregate.py` | Unit tests for the pure aggregation helpers |
| `src/etl/data_processing/tests/test_gold_aggregator.py` | Unit tests for the strategies + `GoldAggregator` (includes the golden-master gate) |
| `src/etl/data_processing/tests/test_gold_aggregation_lambda.py` | Integration tests with moto S3 mocking |

### Design

Each dashboard dataset (`price_time_series_neighborhood`, `rent_vs_sale_ratio`, `boxplot_by_neighborhood`, …)
is modelled as an **Aggregation Strategy** — a small class exposing `key` + `compute(df)` behind one
common `Protocol` (**Open/Closed**, **Polymorphism**): a new dataset plugs in by adding a class and
listing it in `default_populations()`, never by editing a switch or `isinstance` ladder. `GoldAggregator`
**composes** the strategies for the `general`/`relevant` population blocks, depends only on the
`ObjectStore` protocol (**Dependency Inversion**), and assembles the frozen schema-v1.0 document. The
numeric implementations stay as pure functions in `gold_aggregate.py`, called unchanged by the
strategies — the **golden-master test** (`test_gold_aggregator.py`) asserts the resulting JSON is
byte-for-byte identical to the pre-refactor output. `gold_aggregation_lambda.py` is reduced to a
**Factory** (`build_aggregator`) plus a thin call to `aggregate()`.

---

## Aggregations

### Scope Filter

Only listings from the three city-centre districts are included:

```python
SCOPE_DISTRICTS = ["Extramurs", "Ciutat Vella", "L'Eixample"]
```

### Two Populations

All aggregations are computed for **two symmetric populations**:

| Population | Filter |
|---|---|
| `general` | All scoped listings (no additional filter) |
| `relevant` | "Apartments like ours": `hasLift=True`, `floor != "1"`, `size > 120 m²`, `rooms >= 2`, `bathrooms >= 2` |

### Datasets per Population

**`general` population:**
- `price_time_series_neighborhood` — weekly mean `priceByArea` per `(operation, district, neighborhood, snapshot_date)`
- `price_time_series_district` — weekly count-weighted mean per `(operation, district, snapshot_date)` (NOT mean-of-means)
- `rent_vs_sale_ratio` — full-history ratio `mean_priceByArea_sale / mean_priceByArea_rent` per neighborhood
- `rent_vs_sale_ratio_time_series` — same ratio computed per `snapshot_date`
- `boxplot_by_neighborhood` — 5-number summary (min/q1/median/q3/max + count) per `(operation, neighborhood)` over full history
- `boxplot_by_neighborhood_last_3m` — rolling 3-month 5-number summary per `(operation, neighborhood)` (see [Rolling 3-Month KPI Boxplot](#rolling-3-month-kpi-boxplot) below)

**`relevant` population:**
- `rent_vs_sale_ratio`
- `rent_vs_sale_ratio_time_series`
- `boxplot_by_neighborhood`
- `boxplot_by_neighborhood_last_3m`

### Deduplication

Dedup is applied **only within** `(operation, snapshot_date, propertyCode)` — NOT globally. This preserves time-series: the same property appearing in multiple weekly snapshots contributes to each snapshot's aggregate point.

### Ratio Min-Count

Neighborhoods where either the sale or rent side has fewer than `min_count` listings (default: **5**) are excluded from ratio datasets. Configurable via the `RATIO_MIN_COUNT` Lambda environment variable.

### Rolling 3-Month KPI Boxplot

`boxplot_by_neighborhood_last_3m` (FEATURE-010) is an **additive** sibling of the frozen, all-time `boxplot_by_neighborhood` field. It exists so the dashboard's median rent/sale KPI tiles reflect *current* market conditions instead of the full multi-year history.

- **Constant:** `ROLLING_KPI_WINDOW_MONTHS = 3` in `gold_aggregate.py` is the single named source of the window length — no literal `3`/`90-day` window is duplicated elsewhere in the aggregation logic.
- **Window anchor:** the window is relative to `max(snapshot_date)` in the scoped/deduped silver data for that population — **never wall-clock "now"** — so backfills and delayed collection runs stay deterministic.
- **Inclusive lower boundary:** a row is in-window when `snapshot_date >= max(snapshot_date) - 3 calendar months`. A listing snapshotted exactly on the boundary date is included.
- **`min_count` applies inside the window:** the same stability guard used for ratio datasets (default **5**, configurable via `RATIO_MIN_COUNT`) is applied to `(operation, district, neighborhood)` groups computed *within* the rolling window. Sparse recent groups (fewer than `min_count` listings in the last 3 months) are excluded from `boxplot_by_neighborhood_last_3m`, even though the same group may still appear in the all-time `boxplot_by_neighborhood` (which is never filtered by `min_count`).
- **Short history:** when fewer than 3 months of history exist, the window naturally includes every available row; the same `min_count` rule still applies, so a neighborhood with too few total listings is excluded just as it would be with a longer history.
- **Shared math, no duplication:** both `boxplot_by_neighborhood` and `boxplot_by_neighborhood_last_3m` delegate to the same private quantile/groupby helper in `gold_aggregate.py` — the rolling variant only pre-filters rows by date and passes a `min_count` threshold. This avoids two independently-maintained boxplot implementations.
- **`boxplot_by_neighborhood` remains all-time and unfiltered by `min_count`** — its meaning is unchanged from schema-v1.0. The rolling field is purely additive; existing consumers of `boxplot_by_neighborhood` (e.g. the all-time box-and-whisker chart) are unaffected.

---

## Data Basis Blocks (FEATURE-011, FEATURE-016)

`data_basis` is an **additive top-level sibling** of `general`/`relevant`, introduced to power a
"Data Basis" dashboard tab that explains *how* the underlying data was collected (search
parameters, weekly collection volume, and listing distributions) — schema-version stays `"1.0"`.

**Key differences from `general`/`relevant`:**

- **Scoped, like `general`/`relevant` (since 2026-07-18).** Data Basis datasets are computed over
  the same `apply_scope()`-filtered silver history as `general`/`relevant` — only the 3 scope
  districts (`SCOPE_DISTRICTS`). This keeps the neighbourhoods shown on the Data Basis tab (e.g. the
  listing-locations map legend) identical to the neighbourhoods available on the Trend Analysis tab
  (operator decision: Data Basis must never show *more* neighbourhoods than Trend Analysis).
- **Two dedup semantics, chosen per dataset:**
  - **Per-snapshot dedup** (`_dedup`, identical to `general`/`relevant`) for
    `weekly_listing_volume` — the same property re-listed in a later week must still contribute to
    each week's count.
  - **Latest-by-property dedup** (`latest_by_property`) for `size_histogram_10sqm`,
    `rooms_distribution`, `price_per_area_histogram`, and `listing_location_grid_last_3m` — these
    describe "what's currently on the market", so only the most recent snapshot per
    `(operation, propertyCode)` is kept.
- **Strategy classes live in `gold_aggregator.py` only.** The legacy pure entry point
  `gold_aggregate.build_aggregation_json()` intentionally does **not** emit `data_basis` — only
  `GoldAggregator` (the production path used by `gold_aggregation_lambda.py`) does.
- **Count datasets carry `district` and `neighborhood` (FEATURE-016).** `weekly_listing_volume`,
  `size_histogram_10sqm`, `rooms_distribution` and `price_per_area_histogram` are grouped by
  district and neighbourhood in addition to their own grain, so the dashboard can filter them by
  scope. Summing `count_listings` over the two fields yields the count at the dataset's own grain.
  Silver drops rows without a neighbourhood, so no listing is lost by the extra grouping.

### `data_basis_relevant` (FEATURE-016)

`data_basis_relevant` is a second additive top-level block, emitted directly after `data_basis`. It
holds **the same datasets with the same fields**, computed over the relevant population only
(`relevant_filter`), so the Data Basis tab can follow the population toggle. `search_config` is
identical in both blocks.

The relevant filter runs **before** each strategy's dedup, exactly as for the `relevant` block. A
listing whose newest snapshot no longer matches the filter is therefore still counted through its
last matching snapshot.

`data_basis` keeps its unfiltered meaning and `schema_version` stays `"1.0"`: both changes are
additive fields or keys. Consumers that sum counts must group by their own grain; consumers that
read a document written before FEATURE-016 find neither the two location fields on the count
datasets nor the `data_basis_relevant` key.

### Datasets

| Dataset | Dedup | Description |
|---|---|---|
| `search_config` | n/a (static) | Public, stable serialization of the shared Idealista search parameters (see [Search Config](#search-config) below). Always exactly one record. |
| `weekly_listing_volume` | per-snapshot | Listing counts per `(operation, district, neighborhood, snapshot_date)`, scoped to the 3 scope districts. |
| `size_histogram_10sqm` | latest-by-property | Listing counts binned into deterministic 10 m² buckets, per `(operation, district, neighborhood)`. |
| `rooms_distribution` | latest-by-property | Listing counts per `(operation, district, neighborhood, rooms)`. |
| `price_per_area_histogram` | latest-by-property | Listing counts per `(operation, district, neighborhood)` binned by `priceByArea` (EUR/m²), with **operation-specific bin widths** (sale: 250 EUR/m², rent: 1 EUR/m²) — sale and rent price/m² live on very different scales, so sharing bin edges would make one side unreadable. |
| `listing_location_grid_last_3m` | latest-by-property + rolling window | Privacy-safe geo aggregate for the map (see [Privacy-Safe Location Grid](#privacy-safe-location-grid) below). |
| `listing_locations_last_3m` | latest-by-property + rolling window | Raw (unrounded) per-listing coordinates for the real-map view (see [Per-Listing Locations](#per-listing-locations) below). Operator decision (2026-07-18): precise per-listing location disclosure is acceptable for this project. |

### Search Config

Both the bronze-layer collector (`bronze_collector.SearchConfig`, which builds the actual Idealista
API request) and this dataset read from **one shared constant module**,
`src/etl/common/search_config.py` (`IDEALISTA_SEARCH_PARAMS`) — there is no second, independently
maintained copy of the search radius, size range, property type, or elevator/preservation filters.

`search_config_summary()` re-shapes the shared constant into a small, **stable public schema** so
the dashboard never depends on collector-internal field names:

```json
{
  "center_lat": 39.4693441,
  "center_lon": -0.379561,
  "distance_m": 1500,
  "min_size_m2": 100,
  "max_size_m2": 160,
  "elevator": true,
  "air_conditioning": true,
  "preservation": "good",
  "property_type": "homes",
  "sale_credential_label": "LVW",
  "rent_credential_label": "PMV"
}
```

> **Note:** `air_conditioning` documents the *intended* search filter (matches the historical
> hardcoded value in `bronze_collector.SearchConfig`). It is **not currently sent** as an Idealista
> API query parameter — that line is deliberately commented out in `SearchConfig.build_url()`
> (operator decision 2026-07-18: leave live collection behaviour unchanged). Listings both with and
> without air conditioning are collected today.

### Privacy-Safe Location Grid

`listing_location_grid_last_3m` powers the dashboard's schematic map **without ever exposing an
individual listing's location**:

- **Rolling 3-month window.** Reuses the FEATURE-010 rolling-window helper
  (`_rolling_window_start` / `ROLLING_KPI_WINDOW_MONTHS`) — the window is anchored to
  `max(snapshot_date)` in the data, never wall-clock "now".
- **Latest-by-property dedup**, so a re-listed property contributes only its most recent location.
- **Coordinates are rounded to 3 decimal degrees (≈ 80–110 m in Valencia) BEFORE grouping.** Rows
  are then grouped by `(operation, district, neighborhood, latitude_rounded, longitude_rounded)`
  and only the **count** of listings per cell is emitted.
- **Only rounded/aggregated grid cells with counts are ever emitted.** Every record has *exactly*
  these keys — `operation`, `district`, `neighborhood`, `latitude`, `longitude`,
  `count_listings` — and **never** `propertyCode`, address-like fields, exact `price`, or an
  unrounded coordinate. This is enforced by an explicit test
  (`TestListingLocationGridLast3Months::test_never_emits_forbidden_fields` in
  `test_gold_aggregate.py`) asserting the forbidden fields are absent from every record.

Example record:

```json
{
  "operation": "sale",
  "district": "Extramurs",
  "neighborhood": "La Petxina",
  "latitude": 39.474,
  "longitude": -0.39,
  "count_listings": 7
}
```

### Per-Listing Locations

`listing_locations_last_3m` powers the real street-map view on the Data Basis tab, with one point
per currently-listed property:

- Reuses the same rolling 3-month window and latest-by-property dedup as
  `listing_location_grid_last_3m`.
- **Coordinates are emitted exactly as-is** (not rounded/aggregated) — this is an explicit operator
  decision (2026-07-18) that precise per-listing location disclosure is acceptable for this
  project, in exchange for a much more legible, real-map visualization (street-level basemap tiles,
  colored per neighborhood) than the schematic grid allows.
- Still excludes `propertyCode`, address, and price — every record has *exactly* these keys:
  `operation`, `district`, `neighborhood`, `latitude`, `longitude`.
- Kept as an **additive sibling field** to `listing_location_grid_last_3m` (which is unchanged) so
  any future consumer that prefers the privacy-safe aggregate still has it available.

Example record:

```json
{
  "operation": "sale",
  "district": "Extramurs",
  "neighborhood": "La Petxina",
  "latitude": 39.4738216,
  "longitude": -0.3902541
}
```

### Sample `data_basis` Block

```json
{
  "search_config": [
    {
      "center_lat": 39.4693441,
      "center_lon": -0.379561,
      "distance_m": 1500,
      "min_size_m2": 100,
      "max_size_m2": 160,
      "elevator": true,
      "air_conditioning": true,
      "preservation": "good",
      "property_type": "homes",
      "sale_credential_label": "LVW",
      "rent_credential_label": "PMV"
    }
  ],
  "weekly_listing_volume": [
    { "operation": "sale", "district": "Extramurs", "neighborhood": "La Petxina", "snapshot_date": "2023-04-09", "count_listings": 42 }
  ],
  "size_histogram_10sqm": [
    { "operation": "sale", "district": "Extramurs", "neighborhood": "La Petxina", "bin_start_m2": 100, "bin_end_m2": 110, "count_listings": 15 }
  ],
  "rooms_distribution": [
    { "operation": "sale", "district": "Extramurs", "neighborhood": "La Petxina", "rooms": 3, "count_listings": 28 }
  ],
  "price_per_area_histogram": [
    { "operation": "sale", "district": "Extramurs", "neighborhood": "La Petxina", "bin_start_price_m2": 2250.0, "bin_end_price_m2": 2500.0, "count_listings": 9 }
  ],
  "listing_location_grid_last_3m": [
    { "operation": "sale", "district": "Extramurs", "neighborhood": "La Petxina", "latitude": 39.474, "longitude": -0.39, "count_listings": 7 }
  ],
  "listing_locations_last_3m": [
    { "operation": "sale", "district": "Extramurs", "neighborhood": "La Petxina", "latitude": 39.4738216, "longitude": -0.3902541 }
  ]
}
```

---

## JSON Contract v1.0

This schema is **frozen**. FEATURE-005 depends on this exact shape. Do not change field names or structure without bumping `schema_version`.

### Top-Level Structure

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-06-08T12:45:33.123456+00:00",
  "scope_districts": ["Extramurs", "Ciutat Vella", "L'Eixample"],
  "min_count": 5,
  "relevant_filter": {
    "hasLift": true,
    "floor_not": "1",
    "size_gt": 120,
    "rooms_gte": 2,
    "bathrooms_gte": 2
  },
  "general": { ... },
  "relevant": { ... },
  "data_basis": { ... },
  "data_basis_relevant": { ... }
}
```

### Population Block

```json
{
  "price_time_series_neighborhood": [
    {
      "operation": "sale",
      "district": "Extramurs",
      "neighborhood": "Patraix",
      "snapshot_date": "2023-04-09",
      "count_listings": 12,
      "mean_priceByArea": 2450.5,
      "mean_size": 128.3,
      "mean_price": 314200.0
    }
  ],
  "price_time_series_district": [
    {
      "operation": "sale",
      "district": "Extramurs",
      "snapshot_date": "2023-04-09",
      "count_listings": 45,
      "mean_priceByArea": 2390.1,
      "mean_size": 130.0,
      "mean_price": 310800.0
    }
  ],
  "rent_vs_sale_ratio": [
    {
      "district": "Extramurs",
      "neighborhood": "Patraix",
      "mean_priceByArea_sale": 2450.5,
      "mean_priceByArea_rent": 10.2,
      "mean_sales_price_by_rent_ratio": 240.2,
      "count_listings_sale": 45,
      "count_listings_rent": 22
    }
  ],
  "rent_vs_sale_ratio_time_series": [
    {
      "district": "Extramurs",
      "neighborhood": "Patraix",
      "snapshot_date": "2023-04-09",
      "mean_priceByArea_sale": 2450.5,
      "mean_priceByArea_rent": 10.2,
      "mean_sales_price_by_rent_ratio": 240.2,
      "count_listings_sale": 12,
      "count_listings_rent": 8
    }
  ],
  "boxplot_by_neighborhood": [
    {
      "operation": "sale",
      "district": "Extramurs",
      "neighborhood": "Patraix",
      "count": 200,
      "min": 1800.0,
      "q1": 2100.0,
      "median": 2400.0,
      "q3": 2750.0,
      "max": 4200.0
    }
  ],
  "boxplot_by_neighborhood_last_3m": [
    {
      "operation": "sale",
      "district": "Extramurs",
      "neighborhood": "Patraix",
      "count": 28,
      "min": 2200.0,
      "q1": 2350.0,
      "median": 2480.0,
      "q3": 2650.0,
      "max": 3100.0
    }
  ]
}
```

> The `relevant` block omits `price_time_series_neighborhood` and `price_time_series_district`.
> `boxplot_by_neighborhood_last_3m` is additive (schema still v1.0): it has the identical record shape as `boxplot_by_neighborhood` but is scoped to the last `ROLLING_KPI_WINDOW_MONTHS` (3) calendar months relative to `max(snapshot_date)`, with `min_count` applied inside the window. See [Rolling 3-Month KPI Boxplot](#rolling-3-month-kpi-boxplot).

### Key Design Decisions

| Decision | Rationale |
|---|---|
| No global dedup | Preserves time-series — same property in multiple weeks contributes to each week's aggregate |
| Count-weighted district mean | Avoids bias from neighborhoods with few listings; `sum(count × mean) / sum(count)` |
| `floor` compared as string `!= "1"` | Idealista API returns floor as a string; numeric comparison would miss this |
| Key is `mean_price` not `mean_prize` | Typo guard; enforced in tests |
| Boxplot ships 5-number summary | Plotly renders box traces from quartiles; raw rows would be too large |

---

## Infrastructure

### Terraform Module

`infrastructure/modules/lambda_gold/` — reusable module, identical pattern to `lambda_silver`.

| Resource | Detail |
|---|---|
| Lambda | `{env}-gold-aggregator`, python3.12, 512 MB, 300 s timeout |
| Layer | `AWSSDKPandas-Python312` (AWS-managed, passed as variable) |
| IAM S3 | Read `silver/idealista/*` only; write `gold/aggregations/*` only |
| EventBridge | `cron(45 12 ? * SUN *)` — every Sunday 12:45 UTC |
| CloudWatch Logs | 30-day retention |
| Alarm | `Errors > 0` → SNS topic |

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `S3_BUCKET` | (required) | S3 bucket name |
| `SILVER_PREFIX` | `silver/idealista` | Input prefix |
| `GOLD_PREFIX` | `gold/aggregations` | Output prefix |
| `RATIO_MIN_COUNT` | `5` | Min listings per side for ratio datasets |
| `SNS_TOPIC_ARN` | (required) | SNS error notification topic |

### S3 Layout

```
s3://<bucket>/
├── bronze/idealista/          ← raw API JSON (FEATURE-001)
├── silver/idealista/
│   ├── operation=rent/
│   │   └── snapshot_date=YYYY-MM-DD/part.parquet
│   └── operation=sale/
│       └── snapshot_date=YYYY-MM-DD/part.parquet
└── gold/aggregations/
    └── latest.json            ← frozen schema v1.0 (this layer)
```

---

## Running the Backfill

After deploying the gold Lambda to dev, trigger a full historical backfill by invoking it once (it reads the complete silver history on every run):

```bash
aws lambda invoke \
    --function-name dev-gold-aggregator \
    --invocation-type RequestResponse \
    --payload '{}' \
    --region eu-central-1 \
    /tmp/gold-response.json && cat /tmp/gold-response.json
```

---

## Testing

```bash
# Unit tests (pure aggregation logic, no AWS)
cd src/etl/data_processing
pytest tests/test_gold_aggregate.py -v

# Integration tests (moto S3 mocking)
pytest tests/test_gold_aggregation_lambda.py -v

# Gated smoke test against the real dev bucket (requires AWS credentials)
RUN_S3_IT=1 pytest tests/test_gold_smoke.py -v
```
