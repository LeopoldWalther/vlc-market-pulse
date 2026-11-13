# Review — FEATURE-016: Data Basis district/neighbourhood dimensions + population split

**Reviewer:** `/reviewer` · **Date:** 2026-11-13 · **Plan:** [FEATURE-016](../plans/FEATURE-016-data-basis-dimensioned-filters.md)
**Verdict:** ⚠️ Changes Recommended

## Summary

The stub is sound and small enough to ship: four gold helpers gain two group-by keys, the aggregator
gains one additive block, and four renderers sum rows back to their bin grain. Two things must
change before implementation. The stub orders backend before frontend, which breaks the deployed
dashboard for as long as the two are out of step (H1). And it leaves the "one collapse function per
renderer or one shared helper" question open; with four identical uses it should be one helper
(M1). Both are encoded in the technical plan.

## Strengths

- ✅ **Additive by design.** `data_basis` keeps its meaning, `data_basis_relevant` sits beside it,
  and no existing field is renamed. `schema_version` can stay `"1.0"`, which matters because
  `frontend/src/data_source.js` rejects any other value.
- ✅ **Fits the Strategy layout.** The four `Aggregation` strategies keep delegating to the same
  pure helpers; `GoldAggregator` needs one more `_run_data_basis()` call and no new class.
- ✅ **`filters.js` stays generic.** Filtering rows and re-aggregating them are separate concerns,
  and the stub keeps them apart.
- ✅ **Regression guard is named up front.** "Unfiltered output must equal today's output" is the
  right first test for every renderer.

## Findings

### 🔴 H1 — Backend-first ordering breaks the live dashboard between deploys

- **Problem:** Once the gold helpers group by district and neighbourhood, each bin appears once per
  neighbourhood. The currently deployed renderers plot rows as they come, so every bin would show
  up to 13 times on the x-axis. Since FEATURE-018, a merge to `main` that touches `src/etl/**`
  applies to dev automatically, while the frontend is deployed by a manual dispatch. Backend-first
  therefore guarantees a broken dev dashboard, and the same window opens in prod.
- **Impact:** Wrong-looking Data Basis charts in dev and prod until the frontend catches up.
- **Recommendation:** Ship the frontend first. The collapse step sums rows to bin grain, so it is a
  no-op on today's undimensioned rows and correct on the new ones — one frontend build handles both
  shapes. The population selection must likewise fall back to `data_basis` when
  `data_basis_relevant` is absent. Deploy order per environment: frontend, then gold.
- **Evidence:** `frontend/src/charts/size_histogram.js` `toTraces()` maps every row to one bar;
  `.github/workflows/terraform-deploy.yml` applies dev on push to `main`.

### 🟡 M1 — Four private collapse functions are duplication, not four semantics

- **Problem:** The stub suggests one private re-aggregation function per renderer. All four do the
  same thing: group rows by a list of key fields and sum `count_listings`.
- **Recommendation:** One pure helper, `sumCountsBy(rows, keyFields)`, in a new
  `frontend/src/reaggregate.js`; each renderer passes its own key fields. The renderer still owns
  the knowledge of its grain, the helper owns the arithmetic (Single Responsibility), and the
  double-counting risk is tested once instead of four times.
- **Effort:** S.

### 🟡 M2 — A population toggle that moves four of five charts is confusing

- **Problem:** The stub keeps `listing-locations-map` on unfiltered `data_basis` while the other
  four charts follow the population toggle.
- **Recommendation:** Build `data_basis_relevant` with the same strategy list as `data_basis` and
  let every Data Basis chart read the selected block. The search-config panel stays on
  `data_basis`. This also removes the `SCOPED_DATA_BASIS_RENDERER_IDS` special case in `app.js`.
- **Effort:** S (less code than the stub's variant).

### 🟡 M3 — Payload grows by roughly a megabyte

- **Problem:** Prod `latest.json` is 2.67 MB today. `weekly_listing_volume` has 377 rows (29 kB);
  across 13 neighbourhoods it becomes about 4,900 rows (~0.5 MB), and the relevant block adds a
  smaller copy plus its own listing locations.
- **Recommendation:** Accept it — CloudFront compresses JSON and the rows are highly repetitive —
  but record the before/after size in the validation task so growth is a measured number.
- **Effort:** S.

### 🟢 L1 — Stale docstrings

- **Suggestion:** `weekly_listing_volume()` still says it is "computed over UNSCOPED data", which
  has been wrong since 2026-07-18. Fix it while editing the function.
- **Why:** Cheap, and the docstring is about to be rewritten anyway.

### 🟢 L2 — Relevant filter runs before latest-by-property dedup

- **Suggestion:** Document that a listing whose newest snapshot no longer matches the relevant
  filter is still counted through its last matching snapshot. This mirrors `general`/`relevant`.
- **Why:** Avoids a "why do the counts differ" question later — safe to skip.

## Alternatives considered

- **New dimensioned datasets beside the old ones** (`size_histogram_10sqm_by_neighborhood`, …) —
  no shape change for existing keys, so no ordering constraint. Trade-off: every dataset is stored
  twice and the frontend carries two code paths forever. Verdict: stick with the plan; H1's
  frontend-first order solves the same problem without the duplication.
- **Filter in the browser from raw listings** — the map already ships per-listing rows. Trade-off:
  would need price and size per listing in the public JSON, which the gold layer deliberately
  withholds. Verdict: no.

## Risks

| Risk | Likelihood | Impact | Severity | Mitigation |
| --- | --- | --- | --- | --- |
| Dashboard shows duplicated bins between deploys | High if backend first | Medium | 🔴 | Frontend first; collapse is shape-agnostic (H1) |
| Collapse double-counts or drops rows | Medium | High | 🟡 | One tested helper (M1); totals-preserved tests on both sides |
| Golden master regenerated with an unintended change | Medium | Medium | 🟡 | Assert `general`/`relevant` unchanged and per-dataset totals equal before regenerating |
| Dimensioned rows lose listings with a missing neighbourhood | Low | Medium | 🟢 | Silver already drops rows without a neighbourhood; totals test guards it |
| Payload growth slows first paint | Low | Low | 🟢 | Measure in 16.7 (M3) |
| Dev auto-apply smoke test is still red (FEATURE-018) | High | Low | 🟡 | Known and unrelated; verify the dev gold output by hand in 16.7 |

## Effort check

- **Plan estimate:** M (~11.5 h)
- **Reviewer estimate:** M (~9.5 h) — confidence Medium
- **Why it differs / hidden complexity:** The shared helper and the uniform population block remove
  code. The golden-master regeneration and the `app.test.js` harness are the swing factors.

## Reuse & conflicts

- **Reuse:** `frontend/src/filters.js` `filterPopulationBlock()` already filters any block whose
  rows carry `district`/`neighborhood`; `gold_aggregate._relevant_rows()` is the population
  predicate; `GoldAggregator._run_data_basis()` runs a strategy list over any DataFrame.
- **Conflict / coordinate with:** FEATURE-018 — merges touching `src/etl/**` trigger a dev apply
  whose smoke job currently fails on a missing IAM permission. Do not treat that failure as a
  FEATURE-016 regression.

## Approval criteria

- **Blockers (must fix):** H1 — done in the technical plan (frontend tasks 16.1–16.3 precede backend
  tasks 16.4–16.5; deploy order stated in 16.7).
- **Recommended:** M1, M2, M3 — all encoded in the technical plan.
- **Optional:** L1 (in 16.4), L2 (in 16.6).

## Next step

The technical plan ([FEATURE-016-technical-plan.yaml](../plans/technical/FEATURE-016-technical-plan.yaml))
encodes H1 and M1–M3. Proceed with:

```
/implementer FEATURE-016
```

---

### Post-implementation notes
*Filled in after the task ships.*

- **Worked well:** <…>
- **Missed in review:** <…>
- **Estimated vs. actual:** <X> vs. <Y>
