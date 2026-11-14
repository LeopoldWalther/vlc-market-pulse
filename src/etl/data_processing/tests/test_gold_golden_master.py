"""
Golden-master regression test for the gold aggregation output (review M1).

Locks the frozen schema-v1.0 contract byte-for-byte. The committed fixtures
are:

- ``fixtures/silver_fixture.json``     : deterministic silver listing rows
  (snapshot_date stored as ISO string, mirroring what the silver Parquet
  actually contains at gold-read time).
- ``fixtures/gold_latest_golden.json`` : the exact bytes the gold Lambda
  would write to ``gold/aggregations/latest.json`` for that input, with
  ``generated_at`` frozen to a fixed instant.

Serialisation mirrors ``gold_aggregation_lambda.lambda_handler`` exactly:
``json.dumps(aggregation, default=str).encode("utf-8")``.

FEATURE-011 (task 11.4): the golden master is produced by
:class:`gold_aggregator.GoldAggregator` — the actual production path — so it
now includes the additive ``data_basis`` top-level block alongside the
unchanged ``general``/``relevant`` population blocks (review H2). The
still-supported pure entry point :func:`gold_aggregate.build_aggregation_json`
intentionally does NOT emit ``data_basis`` (task 11.4 scope is limited to
``GoldAggregator``); :class:`TestGeneralRelevantCompatibility` proves its
``general``/``relevant`` output is still byte-identical in content to the
golden master's ``general``/``relevant`` blocks.

Regenerating the fixtures (ONLY when the schema contract legitimately
changes, never to paper over refactor drift)::

    python tests/test_gold_golden_master.py --regenerate
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from common.object_store import InMemoryObjectStore  # noqa: E402
from gold_aggregate import build_aggregation_json  # noqa: E402
from gold_aggregator import GoldAggregator  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture locations + determinism constants
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SILVER_FIXTURE_PATH = FIXTURES_DIR / "silver_fixture.json"
GOLDEN_MASTER_PATH = FIXTURES_DIR / "gold_latest_golden.json"

# Frozen "now" so generated_at is deterministic and byte-comparable.
_FROZEN_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

# Matches the terraform default ratio_min_count = 5 used in dev + prod.
_MIN_COUNT = 5

_SILVER_PREFIX = "silver/idealista"
_GOLD_PREFIX = "gold/aggregations"


# ---------------------------------------------------------------------------
# Helpers shared by the test and the --regenerate entry point
# ---------------------------------------------------------------------------


def load_silver_fixture() -> pd.DataFrame:
    """
    Load the committed silver fixture rows into a DataFrame.

    ``snapshot_date`` stays a plain ISO string — exactly how the silver
    Lambda serialises it into Parquet and how the gold Lambda receives it.

    Returns:
        DataFrame of deterministic silver listing rows.
    """
    rows = json.loads(SILVER_FIXTURE_PATH.read_text())
    return pd.DataFrame(rows)


def build_golden_bytes(silver_df: pd.DataFrame) -> bytes:
    """
    Produce the exact bytes the gold Lambda would upload for *silver_df*.

    Runs the real production path — :class:`gold_aggregator.GoldAggregator`
    writing through an in-memory :class:`ObjectStore` fake — so the golden
    master locks the bytes the Lambda actually produces, including the
    additive ``data_basis`` block. Freezes ``gold_aggregate.datetime.now`` so
    ``generated_at`` is stable.

    Args:
        silver_df: Combined silver history DataFrame.

    Returns:
        UTF-8 encoded JSON payload.
    """
    store = InMemoryObjectStore()
    buffer = io.BytesIO()
    silver_df.to_parquet(buffer, index=False, engine="pyarrow")
    store.put_bytes(
        f"{_SILVER_PREFIX}/operation=rent/snapshot_date=2023-04-09/part.parquet",
        buffer.getvalue(),
        content_type="application/octet-stream",
    )

    aggregator = GoldAggregator(
        object_store=store,
        silver_prefix=_SILVER_PREFIX,
        gold_prefix=_GOLD_PREFIX,
        min_count=_MIN_COUNT,
    )
    with mock.patch("gold_aggregate.datetime") as frozen_dt:
        frozen_dt.now.return_value = _FROZEN_NOW
        result = aggregator.aggregate()
    return store.get_bytes(result.key)


# ---------------------------------------------------------------------------
# The golden-master gate
# ---------------------------------------------------------------------------


class TestGoldGoldenMaster:
    """Byte-for-byte regression gate for the schema-v1.0 gold output."""

    def test_fixtures_exist(self) -> None:
        """Both committed fixture files must be present in the repo."""
        assert SILVER_FIXTURE_PATH.is_file(), (
            f"Missing {SILVER_FIXTURE_PATH}. Run "
            "'python tests/test_gold_golden_master.py --regenerate'."
        )
        assert GOLDEN_MASTER_PATH.is_file(), (
            f"Missing {GOLDEN_MASTER_PATH}. Run "
            "'python tests/test_gold_golden_master.py --regenerate'."
        )

    def test_output_matches_golden_master_byte_for_byte(self) -> None:
        """
        The GoldAggregator output for the fixed silver fixture must equal the
        committed golden master exactly — any byte of drift fails the gate.

        The committed file is a POSIX text file (trailing newline, enforced
        by the end-of-file-fixer pre-commit hook); the payload compared here
        is everything before that single terminator byte.
        """
        actual = build_golden_bytes(load_silver_fixture())
        expected = GOLDEN_MASTER_PATH.read_bytes()

        assert expected.endswith(b"\n"), (
            "Golden master must be newline-terminated (pre-commit "
            "end-of-file-fixer contract)."
        )
        assert actual == expected[:-1], (
            "Gold output drifted from the committed golden master. "
            "If (and only if) the schema contract intentionally changed, "
            "regenerate via 'python tests/test_gold_golden_master.py "
            "--regenerate' and bump the schema version."
        )

    def test_golden_master_is_schema_v1(self) -> None:
        """Sanity: the golden master itself honours the v1.0 contract."""
        payload = json.loads(GOLDEN_MASTER_PATH.read_bytes())

        assert payload["schema_version"] == "1.0"
        assert payload["min_count"] == _MIN_COUNT
        assert set(payload["general"].keys()) == {
            "price_time_series_neighborhood",
            "price_time_series_district",
            "rent_vs_sale_ratio",
            "rent_vs_sale_ratio_time_series",
            "boxplot_by_neighborhood",
            "boxplot_by_neighborhood_last_3m",
        }
        assert set(payload["relevant"].keys()) == {
            "rent_vs_sale_ratio",
            "rent_vs_sale_ratio_time_series",
            "boxplot_by_neighborhood",
            "boxplot_by_neighborhood_last_3m",
        }
        # Non-trivial content: the fixture must exercise every dataset.
        assert payload["general"]["price_time_series_neighborhood"]
        assert payload["general"]["rent_vs_sale_ratio"]
        assert payload["relevant"]["rent_vs_sale_ratio"]
        assert payload["general"]["boxplot_by_neighborhood_last_3m"]
        assert payload["relevant"]["boxplot_by_neighborhood_last_3m"]

    def test_golden_master_includes_data_basis_block(self) -> None:
        """
        FEATURE-011 (task 11.4): the additive ``data_basis`` top-level block
        must be present with every Data Basis dataset, and schema_version
        must stay "1.0" (additive, not a breaking change).
        """
        payload = json.loads(GOLDEN_MASTER_PATH.read_bytes())

        assert payload["schema_version"] == "1.0"
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
        # Non-trivial content for every new dataset.
        assert payload["data_basis"]["search_config"]
        assert payload["data_basis"]["weekly_listing_volume"]
        assert payload["data_basis"]["size_histogram_10sqm"]
        assert payload["data_basis"]["rooms_distribution"]
        assert payload["data_basis"]["price_per_area_histogram"]
        # This fixture predates latitude/longitude columns (task 11.4 keeps
        # the shared input fixture unchanged) — the geo grid/points are
        # legitimately empty here. Their non-empty behaviour is covered
        # directly by test_gold_aggregate.py / test_gold_aggregator.py.
        assert payload["data_basis"]["listing_location_grid_last_3m"] == []
        assert payload["data_basis"]["listing_locations_last_3m"] == []

    def test_golden_master_includes_data_basis_relevant_block(self) -> None:
        """
        FEATURE-016 (task 16.5): ``data_basis_relevant`` carries the same
        datasets as ``data_basis``, restricted to the relevant population,
        and schema_version stays "1.0" (additive key).
        """
        payload = json.loads(GOLDEN_MASTER_PATH.read_bytes())

        assert payload["schema_version"] == "1.0"
        assert list(payload["data_basis_relevant"].keys()) == list(
            payload["data_basis"].keys()
        )
        # Non-trivial content: the fixture contains relevant listings.
        assert payload["data_basis_relevant"]["weekly_listing_volume"]

        def total(block: str) -> int:
            return sum(
                row["count_listings"] for row in payload[block]["weekly_listing_volume"]
            )

        # The relevant population is a strict subset of the fixture.
        assert 0 < total("data_basis_relevant") < total("data_basis")


class TestGeneralRelevantCompatibility:
    """
    H2: adding ``data_basis`` must not change the ``general``/``relevant``
    population blocks in any way.

    Diff-check method: :func:`build_aggregation_json` is the pure,
    pre-FEATURE-011 entry point that never learned about ``data_basis`` (it
    is intentionally out of task 11.4's scope). If its ``general``/
    ``relevant`` output is still exactly equal — key-for-key, value-for-value
    — to the committed golden master's ``general``/``relevant`` blocks, the
    additive change did not alter existing behaviour.
    """

    def test_general_and_relevant_blocks_are_dict_equal_to_golden_master(
        self,
    ) -> None:
        golden = json.loads(GOLDEN_MASTER_PATH.read_bytes())

        with mock.patch("gold_aggregate.datetime") as frozen_dt:
            frozen_dt.now.return_value = _FROZEN_NOW
            pure_document = build_aggregation_json(
                load_silver_fixture(), min_count=_MIN_COUNT
            )
        # Round-trip through JSON so both sides use the same (str, float,
        # int) representations — an apples-to-apples content comparison.
        pure_as_json = json.loads(json.dumps(pure_document, default=str))

        assert pure_as_json["general"] == golden["general"]
        assert pure_as_json["relevant"] == golden["relevant"]
        assert pure_as_json["schema_version"] == golden["schema_version"]
        assert pure_as_json["scope_districts"] == golden["scope_districts"]
        assert pure_as_json["min_count"] == golden["min_count"]
        assert pure_as_json["relevant_filter"] == golden["relevant_filter"]
        # The pure legacy entry point is intentionally NOT wired to
        # data_basis (task 11.4 scope is GoldAggregator only).
        assert "data_basis" not in pure_as_json


# ---------------------------------------------------------------------------
# Deterministic fixture generation (--regenerate)
# ---------------------------------------------------------------------------

_SNAPSHOTS = ["2023-04-09", "2023-04-16"]
_OPERATIONS = ["sale", "rent"]

# (district, neighborhood) — three in-scope locations (note the typographic
# apostrophe U+2019 in L’Eixample, asserted by the schema tests) plus one
# out-of-scope district to exercise apply_scope.
_LOCATIONS = [
    ("Extramurs", "La Petxina"),
    ("Ciutat Vella", "El Carme"),
    ("L\u2019Eixample", "Russafa"),
    ("Campanar", "Nou Moles"),
]

# 10 listings per (snapshot, operation, location); every second one matches
# the relevant-population predicate → 5 relevant per side == _MIN_COUNT, so
# the relevant ratio datasets are populated (not filtered away).
_LISTINGS_PER_GROUP = 10


def _generate_silver_fixture_rows() -> List[Dict[str, Any]]:
    """
    Build the deterministic silver fixture rows (no randomness, no clock).

    Returns:
        List of silver listing dicts covering both operations, two
        snapshots, all scope districts, one out-of-scope district, both
        populations (relevant + non-relevant) and one duplicated
        propertyCode to exercise dedup.

        NOTE (FEATURE-011, task 11.4): this fixture predates the
        ``latitude``/``longitude`` columns, so
        ``data_basis.listing_location_grid_last_3m`` is legitimately empty
        for this golden master — the geo-grid logic itself is fully covered
        (including non-empty cases) by ``test_gold_aggregate.py`` and
        ``test_gold_aggregator.py``. The input fixture is intentionally left
        unchanged here (task 11.4's allowed_files scope covers only the
        golden output, not this shared input fixture).
    """
    rows: List[Dict[str, Any]] = []
    for snap_idx, snapshot in enumerate(_SNAPSHOTS):
        for op_idx, operation in enumerate(_OPERATIONS):
            base_price = 300_000.0 if operation == "sale" else 1_200.0
            step = 10_000.0 if operation == "sale" else 40.0
            for loc_idx, (district, neighborhood) in enumerate(_LOCATIONS):
                for i in range(_LISTINGS_PER_GROUP):
                    relevant = i % 2 == 0
                    size = (130.0 if relevant else 105.0) + 2.0 * i
                    price = round(
                        base_price * (1 + 0.02 * snap_idx)
                        + 1_000.0 * loc_idx
                        + step * i,
                        2,
                    )
                    rows.append(
                        {
                            "operation": operation,
                            "district": district,
                            "neighborhood": neighborhood,
                            "snapshot_date": snapshot,
                            "propertyCode": f"P{snap_idx}{op_idx}{loc_idx}{i}",
                            "priceByArea": round(price / size, 2),
                            "size": size,
                            "price": price,
                            "floor": "3" if relevant else "1",
                            "rooms": 3 if relevant else 1,
                            "bathrooms": 2 if relevant else 1,
                            "hasLift": relevant,
                        }
                    )

    # One exact duplicate within the same (operation, snapshot_date,
    # propertyCode) so the dedup path contributes to the golden master.
    rows.append(dict(rows[0]))
    return rows


def main() -> None:
    """Regenerate both fixtures deterministically and report their sizes."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    rows = _generate_silver_fixture_rows()
    SILVER_FIXTURE_PATH.write_text(json.dumps(rows, indent=2) + "\n")
    print(f"Wrote {len(rows)} rows to {SILVER_FIXTURE_PATH}")

    golden = build_golden_bytes(load_silver_fixture())
    # Trailing newline: keep the committed file POSIX-text so the
    # end-of-file-fixer pre-commit hook never rewrites it.
    GOLDEN_MASTER_PATH.write_bytes(golden + b"\n")
    print(f"Wrote {len(golden)} payload bytes to {GOLDEN_MASTER_PATH}")


if __name__ == "__main__":
    if "--regenerate" not in sys.argv:
        sys.exit("Refusing to regenerate without the explicit --regenerate flag.")
    main()
