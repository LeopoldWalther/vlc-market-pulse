# FEATURE-013 — Pipeline Health tab: detailed views & history

**Status:** 🟢 Complete — deployed to dev and prod · **Effort:** L (~33–34 h) · **Priority:** Medium
**Branch root:** `feature/pipeline-health-detail-views` · **Created:** 2026-07-18 · **Updated:** 2026-07-18

> Authored by `@architect`. Reviewed by `@reviewer` (see `dev/reviews/REVIEW-FEATURE-013.md`).
> Implemented by `@implementer` from `dev/plans/technical/FEATURE-013-technical-plan.yaml`.
> Numbering verified (task 13.1): this feature is consistently FEATURE-013 across the top-level
> plan, `dev/plans/technical/FEATURE-013-technical-plan.yaml`, and the `dev/plans/README.md`
> registry/dependency graph — no stray `FEATURE-014` references exist.

## Objective

Turn the Pipeline Health tab from a single overall badge + 4 flat one-line rows (FEATURE-012) into
a detailed operational view: a Medallion pipeline topology diagram, 4 historical charts (one per
Ampel rule), and an explicit, user-facing statement of each rule's green/yellow/red thresholds —
without inventing new infrastructure, new dependencies, or a new JSON endpoint.

## Context

FEATURE-012 (🟢 implemented, deployed dev+prod) added `gold/pipeline_health/latest.json`
(`schema_version: "1.0"`), written weekly by `PipelineHealthAggregator`
(`src/etl/pipeline_health/pipeline_health_aggregator.py`) from 4 Strategy `HealthCheck` classes in
`src/etl/pipeline_health/health_checks.py`:

- `ExecutionSuccessCheck` / `ExecutionDurationCheck` — share a bounded-polling CloudWatch Logs
  Insights adapter (`_LogsInsightsExecutionHistory.fetch()`) that already queries and parses the
  last `EXECUTION_HISTORY_WINDOW = 5` `REPORT` log events per Lambda into `_InvocationRecord`
  (`duration_seconds`, `succeeded`) — **but the per-invocation records are discarded after being
  reduced to an aggregate** (`invocations_checked`, `max_duration_seconds`) in
  `_evaluate_one()`. The Logs Insights query string already selects `@timestamp` in its `display`
  clause, but `_parse_row()` never reads it into `_InvocationRecord` — it is dropped on the floor
  today, not merely unqueried. This means **no new Logs Insights query is required** to expose
  per-invocation history — only a parsing/serialization change.
- `ApiQuotaCheck` — already computes and returns a genuine 5-month history
  (`details.credential_sets[LVW|PMV].monthly_requests: {"YYYY-MM": count}`) via
  `CloudWatch GetMetricData`. **No backend change needed** — this KPI's chart is a pure frontend
  consumption task.
- `AwsCostCheck` — computes only a **single current month-to-date total**
  (`details.included_total_usd`) via one `ce.GetCostAndUsage` call, `GroupBy=[SERVICE]`, no history
  retained beyond the aggregate. A 5-month per-service history requires one additional Cost
  Explorer call.

Frontend: `frontend/src/pipeline_health.js` (pure formatting helpers, no DOM/fetch),
`frontend/src/pipeline_health_data_source.js` (fetch + `schema_version` guard, degrades to `null`
on any failure), `frontend/index.html` (`#panel-pipeline-health` — currently just an overall badge
`<div>` + a `<ul>` of one-line sublight rows), `frontend/src/i18n.js` (5 locales: en/de/es/ar/tr),
`frontend/styles.css` (design tokens: `--color-status-{green,yellow,red}[-bg]` in `:root` /
`[data-theme="dark"]`, per FEATURE-009's token system). Charts elsewhere in the dashboard
(`frontend/src/charts/*.js`) follow one convention: a plain object `{ id, title, render(data,
context) → {data, layout} }`, pure (no DOM/fetch), built on `buildLayout()` from
`frontend/src/chart_theme.js` for viewport/theme-aware Plotly layout, unit-tested with Vitest
(`frontend/tests/*.test.js`) by asserting on the returned trace/layout shape, never against a
live Plotly render. Plotly is vendored locally (`frontend/vendor/`) — no new dependency is needed
for the 4 KPI charts.

The original threshold rules (quoted verbatim from `dev/plans/FEATURE-012-pipeline-health-monitoring.md`,
confirmed unchanged since implementation — see `dev/reviews/REVIEW-FEATURE-012.md` and
`src/etl/pipeline_health/health_checks.py` constants):

1. **Execution success** (per Lambda function, worst of the 3 functions wins, window = last 5
   invocations):
   - 🔴 RED — the most recent invocation failed.
   - 🟡 YELLOW — the most recent invocation succeeded, but at least 1 of the 4 invocations
     immediately before it failed.
   - 🟢 GREEN — all 5 of the last 5 invocations succeeded.
2. **Execution duration** (per Lambda function, last 5 invocations, worst of the 3 functions wins):
   - 🟢 GREEN — every invocation completed in under 5 minutes.
   - 🟡 YELLOW — at least one invocation took 5–10 minutes.
   - 🔴 RED — at least one invocation took over 10 minutes.
3. **API quota** (per credential set — LVW = sale, PMV = rent — worst of the 2 sets wins, over the
   last 5 fully-completed calendar months, excluding the current in-progress month):
   - 🟢 GREEN — every evaluated month used < 80 of the 100 requests/month quota.
   - 🟡 YELLOW — at least one month used ≥ 80%.
   - 🔴 RED — at least one month used ≥ 95%.
4. **AWS cost** (project-wide, month-to-date, excluding domain/registrar costs):
   - 🟢 GREEN — < $2/month.
   - 🟡 YELLOW — ≥ $2/month.
   - 🔴 RED — ≥ $5/month.

These constants (`EXECUTION_HISTORY_WINDOW`, `DURATION_YELLOW/RED_THRESHOLD_SECONDS`,
`API_QUOTA_YELLOW/RED_THRESHOLD_REQUESTS`, `AWS_COST_YELLOW/RED_THRESHOLD_USD`) already exist in
`health_checks.py` and are the **single source of truth** the frontend threshold captions must
describe — no new numbers are introduced, only new visibility.

## Dependencies

- **Needs:** FEATURE-012 — Pipeline Health monitoring (schema, checks, tab, deployed dev+prod).
- **Unblocks:** nothing directly; a natural follow-up is FEATURE-012's deferred SNS red-status
  alerting (review L1), which becomes easier to reason about once history is visible in the UI.

## Design & patterns

No new architectural layer is introduced — this feature enriches an existing Strategy/Adapter
design and adds pure, stateless rendering functions, consistent with the "avoid over-engineering"
principle:

- **Strategy (existing, reused):** `ExecutionSuccessCheck`/`ExecutionDurationCheck`/`ApiQuotaCheck`/
  `AwsCostCheck` remain the 4 `HealthCheck` Strategy implementations. No new Strategy is added —
  this feature enriches their `details` payloads, it does not change how the overall Ampel status
  is composed (`worst_status` is untouched).
- **Adapter (existing, reused):** `_LogsInsightsExecutionHistory` already wraps
  `start_query`/`get_query_results`; it gains one more parsed field (`timestamp`) on
  `_InvocationRecord`, not a new adapter.
- **Adapter (new, narrow):** `_CostExplorerMonthlyHistory` — a small, single-purpose Adapter around
  one `ce.GetCostAndUsage` call (`Granularity="MONTHLY"`, `GroupBy=[SERVICE]`, 5-month time window),
  injected into `AwsCostCheck` exactly like `_LogsInsightsExecutionHistory` is injected into the
  execution checks (same constructor-injection shape, same "tests inject a fake/stub, boto3 stays
  at the edge" rule). This keeps `AwsCostCheck.evaluate()`'s Single Responsibility ("evaluate rule
  4's status") separate from "fetch 5 months of service-level cost history" (Single Responsibility,
  Dependency Inversion — `AwsCostCheck` depends on the adapter's narrow interface, not on
  `boto3.client("ce")` directly, exactly as it already does for the month-to-date call).
- **Template/shared helper (existing, reused):** both execution checks already share one
  `_evaluate_one()`-shaped flow per function; `recent_invocations` is added to both `detail` dicts
  from the same already-fetched `records` list — no duplication of Logs Insights calls.
- **Factory (existing, reused):** the Lambda handler factory (`pipeline_health_lambda.py`)
  constructs both boto3 clients (`logs`, `ce`) and the 4 checks; it gains one extra client-injection
  line (the new `_CostExplorerMonthlyHistory` reuses the same `ce` client already constructed at
  `region_name="us-east-1"`, review M1 — no new IAM permission beyond the existing
  `ce:GetCostAndUsage` action already granted, since it is the same API call with a different time
  window/granularity).
- **Custom exception (existing, reused):** `LogsInsightsQueryError` continues to be the only
  exception surfaced by the shared Logs Insights adapter; `_CostExplorerMonthlyHistory` reuses the
  existing "catch and degrade to an empty/partial history rather than raise" convention already
  used by `_safe_evaluate()` at the aggregator level — no new exception type needed for a single
  best-effort extra API call.
- **Pure functions, not classes, on the frontend (existing convention, reused):** each new chart
  renderer is a plain `{ id, title, render(document, context) → {data, layout} }` object — a
  Factory-like object literal, not a class hierarchy, matching every existing file in
  `frontend/src/charts/`. The Medallion diagram is likewise two pure functions
  (`buildMedallionDiagramModel(document)` → node/edge model, `renderMedallionDiagramSvg(model,
  locale)` → SVG markup string) — a **Template Method-shaped** two-step "build data, then render"
  split chosen so the model-building half is unit-testable without touching a DOM, mirroring how
  `pipeline_health.js`'s existing `buildSubLightRows()` is unit-tested without a DOM today.
- **Rejected:** a class-based `ChartRenderer` hierarchy with polymorphic `render()` overrides — the
  4 KPI charts have no shared *behaviour*, only a shared *shape* (an object literal satisfying a
  duck-typed interface), so a class hierarchy would add indirection without solving a real
  variability problem — the existing `frontend/src/charts/*.js` convention (object literals) is
  reused unchanged. A new charting library was also rejected — Plotly (already vendored) covers bar
  and scatter/dot-timeline charts, so no new dependency is needed for any of the 4 KPI charts; the
  diagram is plain SVG/HTML, avoiding a 5th dependency (e.g. D3) for a single static topology.

## Schema change: `gold/pipeline_health/latest.json` v1.0 → v1.1

Additive only — no existing key is removed or renamed, so `schema_version` bumps from `"1.0"` to
`"1.1"` (backward-compatible enrichment, same precedent as any additive schema change in this
project; the frontend's `PipelineHealthDataSource` schema guard is updated to accept `"1.1"`).

```jsonc
{
  "schema_version": "1.1",
  // ... generated_at, overall_status unchanged ...
  "execution_success": {
    // ... status, summary, evaluated_at unchanged ...
    "details": {
      "functions": {
        "prod-gold-aggregator": {
          "status": "green",
          "invocations_checked": 5,
          // NEW — newest-first, same records already fetched by
          // _LogsInsightsExecutionHistory.fetch(), just no longer discarded:
          "recent_invocations": [
            {"timestamp": "2026-06-14T12:45:31+00:00", "succeeded": true, "duration_seconds": 25.4},
            {"timestamp": "2026-06-07T12:45:29+00:00", "succeeded": true, "duration_seconds": 22.1},
            {"timestamp": "2026-05-31T12:45:33+00:00", "succeeded": true, "duration_seconds": 24.8},
            {"timestamp": "2026-05-24T12:45:30+00:00", "succeeded": true, "duration_seconds": 23.0},
            {"timestamp": "2026-05-17T12:45:28+00:00", "succeeded": true, "duration_seconds": 21.9}
          ]
        }
      }
    }
  },
  "execution_duration": {
    // same "recent_invocations" array added per function, independently
    // (each check's details block stays self-sufficient for its own chart —
    // trivial duplication of ≤5 small records, not worth a cross-reference)
  },
  "api_quota": {
    // UNCHANGED — monthly_requests history already present, see FEATURE-012
  },
  "aws_cost": {
    "details": {
      "included_total_usd": 1.66,
      "excluded_total_usd": 0.0,
      "excluded_services": [],
      "excluded_services_configured": ["Amazon Registrar", "Amazon Route 53 Domains"],
      // NEW — last 5 fully-completed calendar months (current month excluded,
      // same "avoid partial-month bias" rule already used by ApiQuotaCheck),
      // oldest-first, per-service, already excluding the configured services:
      "monthly_cost_by_service": {
        "2026-01": {"AWS Lambda": 0.02, "Amazon S3": 0.01, "Amazon CloudFront": 0.00},
        "2026-02": {"AWS Lambda": 0.03, "Amazon S3": 0.01, "Amazon CloudFront": 0.00},
        "2026-03": {"AWS Lambda": 0.04, "Amazon S3": 0.01, "Amazon CloudFront": 0.01},
        "2026-04": {"AWS Lambda": 0.05, "Amazon S3": 0.01, "Amazon CloudFront": 0.01},
        "2026-05": {"AWS Lambda": 0.06, "Amazon S3": 0.02, "Amazon CloudFront": 0.01}
      },
      "monthly_cost_history_months": 5
    }
  }
}
```

## UX description

The `#panel-pipeline-health` tab keeps the existing overall badge + one-line sublight rows at the
top (still the fastest "is everything OK" read), then adds, in order:

1. **Medallion pipeline diagram** — a static-topology SVG showing
   `idealista-collector → silver-cleaner → gold-aggregator` as the 3 medallion-stage nodes, plus a
   4th `pipeline-health` node connected by a dashed line (observer, not a pipeline participant — the
   same distinction FEATURE-012 already documents). Each stage node is colored using
   `execution_success.details.functions[<matching name>].status` when the currently-loaded
   environment's document contains that function name (dev shows `dev-*`, prod shows `prod-*` — the
   diagram strips the environment prefix only for the display label, never for the status lookup
   key, to avoid a dev/prod mismatch bug). If a function name is not found (e.g. a differently-named
   environment), the node falls back to a neutral "unknown" grey — this must never throw.
2. **KPI 1 — Execution success**: one section per monitored function, each a small Plotly
   scatter "sequence of dots" (oldest → newest, left → right) colored green/red per
   `recent_invocations[i].succeeded`, x-axis labelled with the invocation timestamp. A caption below
   states the 3-rule text verbatim (see i18n keys below).
3. **KPI 2 — Execution duration**: one grouped bar chart, one bar group per function, bars = the
   last 5 `duration_seconds` values, with two horizontal reference lines at 5 min (yellow) and
   10 min (red) so the thresholds are visible on the chart itself, in addition to the caption text.
4. **KPI 3 — API quota**: one grouped bar chart, 2 series (LVW=sale, PMV=rent), x-axis = the 5
   months from `monthly_requests`, y-axis = requests, plus a horizontal reference line at
   `quota` (100) and shaded threshold bands at 80/95 (reusing `chart_theme.js` conventions for
   reference-line styling already used elsewhere if such a convention exists, else a plain Plotly
   `shapes` array — confirmed during implementation, see Open Questions).
5. **KPI 4 — AWS cost**: one stacked bar chart, one bar per month (5 months), stacked by
   AWS service from `monthly_cost_by_service`, using `chart_theme.js`'s `colorway` for consistent
   per-service coloring across renders.
6. Each of the 4 KPI sections shows a small "ℹ️ Threshold rules" caption directly beneath its
   chart, sourced from a new i18n key per KPI, quoting the exact rule text above (localized).

All 4 charts and the diagram degrade gracefully: if a `document` is `null` (load failure, matching
existing `loadOrUnavailable()` behavior) or an individual KPI block is missing/malformed, that
section renders an empty chart / a neutral diagram rather than throwing — consistent with
FEATURE-012's "health tab failure must never break other tabs" rule, extended here to
"one KPI section's malformed data must never break the other 3 sections or the diagram."

Mobile layout: KPI sections stack vertically (existing `MOBILE_GEOMETRY` in `chart_theme.js`
already parameterizes margins/legend orientation by viewport — reused, not reinvented); the diagram
switches from a horizontal to a vertical node layout below a width breakpoint, matching the
project's existing mobile-first breakpoint conventions in `styles.css`.

## Approach

### Phase 1 — Backend: expose execution history (TDD)
- [ ] `_InvocationRecord` gains a `timestamp: datetime` field; `_parse_row()` parses the
      already-queried `@timestamp` field (no query string change).
- [ ] `ExecutionSuccessCheck._evaluate_one()` and `ExecutionDurationCheck._evaluate_one()` each add
      `detail["recent_invocations"]` — newest-first list of
      `{"timestamp": iso8601 str, "succeeded": bool, "duration_seconds": float}`, built from the
      same `records` list already fetched (no new Logs Insights query).
- [ ] `pipeline_health_aggregator.SCHEMA_VERSION` bumps `"1.0"` → `"1.1"`.
- [ ] Unit tests: `recent_invocations` shape/order for 0, 1, <5, 5 records; timestamp
      ISO-8601-serializable; existing aggregate fields (`invocations_checked`,
      `max_duration_seconds`) unchanged.

### Phase 2 — Backend: AWS cost 5-month per-service history (TDD)
- [ ] `_CostExplorerMonthlyHistory` Adapter — one `ce.GetCostAndUsage` call,
      `Granularity="MONTHLY"`, `GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}]`, time window =
      last 5 fully-completed calendar months (reuses `_month_start`/`_add_months` helpers already in
      `health_checks.py`), excluding `excluded_services` from the returned per-month dict.
- [ ] `AwsCostCheck` gains an optional injected `history: Optional[_CostExplorerMonthlyHistory]`
      constructor parameter (default: built from the same `cost_explorer_client`); `evaluate()`
      adds `details["monthly_cost_by_service"]` and `details["monthly_cost_history_months"]`
      without changing the existing month-to-date status computation.
- [ ] Unit tests against a stubbed `ce` client (`botocore.stub.Stubber`, matching FEATURE-012's
      existing test pattern): 0 months of data, partial months, excluded-service filtering,
      multiple services per month.
- [ ] Confirm no new IAM permission is required (same `ce:GetCostAndUsage` action, already granted
      to the pipeline-health Lambda role) — a one-line note in the Terraform module comment, no
      `.tf` file changes expected.

### Phase 3 — Frontend: schema guard + pure chart-data helpers (TDD)
- [ ] `PipelineHealthDataSource`: accept `schema_version === '1.1'` (and, deliberately, keep
      rejecting anything else — no silent multi-version fan-out logic for a 2-version history).
- [ ] `pipeline_health.js` gains pure helpers consumed by the chart renderers and by the threshold
      captions: `executionHistorySeries(check, functionNames)`,
      `apiQuotaMonthlySeries(apiQuotaCheck)`, `awsCostMonthlySeries(awsCostCheck)`, and
      `thresholdRuleText(checkId, locale)` (looks up the new i18n keys below) — all null-safe.
- [ ] Unit tests for each helper: empty/missing document, partial data, well-formed document.

### Phase 4 — Frontend: 4 KPI Plotly chart renderers (TDD)
- [ ] `frontend/src/charts/pipeline_execution_success_chart.js` — one scatter trace per function,
      dot markers colored green/red from `recent_invocations[i].succeeded`.
- [ ] `frontend/src/charts/pipeline_execution_duration_chart.js` — one bar trace per function over
      the last 5 `duration_seconds`, with 5 min/10 min reference `shapes`.
- [ ] `frontend/src/charts/pipeline_api_quota_chart.js` — grouped bar (LVW/PMV) over 5 months, with
      a quota reference line.
- [ ] `frontend/src/charts/pipeline_aws_cost_chart.js` — stacked bar by service over 5 months.
- [ ] Vitest coverage per renderer: empty data → empty-but-non-throwing figure; well-formed data →
      correct trace count/x/y shape; snapshot-free assertions (matches existing `charts/*.test.js`
      convention of asserting on structure, not pixel output).

### Phase 5 — Frontend: Medallion diagram
- [ ] `frontend/src/pipeline_health_diagram.js` — `buildMedallionDiagramModel(document)` (pure, no
      DOM) returns `{ nodes: [{id, label, status}], edges: [{from, to, style}] }` for the 3
      medallion stages + 1 observer node; `renderMedallionDiagramSvg(model, locale)` (pure string
      builder) returns the SVG markup.
- [ ] Unit tests: correct node count/order/status for a well-formed document; graceful "unknown"
      status for missing/renamed functions; never throws on a `null` document.

### Phase 6 — Frontend: markup, wiring, styles, i18n
- [ ] `frontend/index.html`: extend `#panel-pipeline-health` with a diagram container and 4 new
      `<section>`s (chart container + threshold-caption `<p>` each), keeping the existing overall
      badge/sublights markup untouched above them.
- [ ] `frontend/app.js`: `renderPipelineHealthTab()` additionally builds and mounts the diagram SVG
      and calls `Plotly.newPlot(...)` for the 4 new chart containers, reusing the existing
      lazy-load-once-per-tab-activation pattern; re-render on locale/theme change reuses the
      existing hooks that already re-render the sublights on locale switch.
- [ ] `frontend/styles.css`: new tokens/rules for the diagram (node/edge styling, status-colored
      node borders reusing the existing `--color-status-*` tokens — no new color tokens needed),
      chart section spacing, and the threshold-caption `<p>` style (small, muted text, consistent
      with existing caption/description patterns e.g. `.data-basis-map-description`); mobile
      breakpoint for the diagram's vertical layout.
- [ ] `frontend/src/i18n.js`: add keys in all 5 locales (en/de/es/ar/tr) for: 4 section titles, 4
      threshold-rule captions (verbatim rule text, localized), diagram node labels
      (`pipelineHealth.diagram.bronze/silver/gold/health`), and an "unknown status" diagram label.

### Phase 7 — Docs & deployment
- [ ] Update `documentation/PIPELINE_HEALTH_LAYER.md`: schema v1.1 fields, the new charts/diagram,
      and a note that the 4 Ampel rules are now also surfaced verbatim in the UI (not only in code
      and docs).
- [ ] Deploy to **dev only** first (Lambda code change via the existing
      `infrastructure/modules/lambda_pipeline_health/` fileset packaging — no new Terraform
      resource expected); manually invoke, download `gold/pipeline_health/latest.json`, confirm
      `schema_version: "1.1"` and the new fields are present and well-formed; verify the frontend
      tab renders the diagram + 4 charts + captions against real dev data (including a
      short-history / insufficient-history dev environment — must degrade gracefully, matching
      FEATURE-012's precedent).
- [ ] Only after dev verification: promote to **prod** (separate deploy step, mirroring FEATURE-012
      and FEATURE-006's two-stage rollout discipline).

## Files

- **Change:**
  - `src/etl/pipeline_health/health_checks.py` — `_InvocationRecord.timestamp`, `_parse_row()`,
    `recent_invocations` in both execution checks' details, new `_CostExplorerMonthlyHistory`
    Adapter, `AwsCostCheck` history wiring.
  - `src/etl/pipeline_health/pipeline_health_aggregator.py` — `SCHEMA_VERSION = "1.1"`.
  - `src/etl/pipeline_health/pipeline_health_lambda.py` — construct/inject
    `_CostExplorerMonthlyHistory` (reuses the existing `ce` client).
  - `src/etl/pipeline_health/tests/test_health_checks.py` — new/updated cases for the above.
  - `frontend/src/pipeline_health_data_source.js` — accept schema `1.1`.
  - `frontend/src/pipeline_health.js` — new pure chart-data + threshold-caption helpers.
  - `frontend/index.html` — extended `#panel-pipeline-health` markup.
  - `frontend/app.js` — mount diagram + 4 charts on tab activation.
  - `frontend/styles.css` — diagram/chart/caption styles + mobile breakpoint.
  - `frontend/src/i18n.js` — new keys, all 5 locales.
  - `documentation/PIPELINE_HEALTH_LAYER.md` — schema v1.1 + new UI documentation.
- **Create:**
  - `frontend/src/pipeline_health_diagram.js` — Medallion diagram model + SVG renderer.
  - `frontend/src/charts/pipeline_execution_success_chart.js`
  - `frontend/src/charts/pipeline_execution_duration_chart.js`
  - `frontend/src/charts/pipeline_api_quota_chart.js`
  - `frontend/src/charts/pipeline_aws_cost_chart.js`
- **Tests:**
  - `src/etl/pipeline_health/tests/test_health_checks.py` (extended)
  - `frontend/tests/pipeline_health.test.js` (extended)
  - `frontend/tests/pipeline_health_data_source.test.js` (extended)
  - `frontend/tests/pipeline_health_diagram.test.js` (new)
  - `frontend/tests/charts/pipeline_execution_success_chart.test.js` (new)
  - `frontend/tests/charts/pipeline_execution_duration_chart.test.js` (new)
  - `frontend/tests/charts/pipeline_api_quota_chart.test.js` (new)
  - `frontend/tests/charts/pipeline_aws_cost_chart.test.js` (new)

## Test strategy

- **Unit (Python):** `_parse_row()` timestamp parsing; `recent_invocations` shape/order for 0, 1,
  <5, 5-record histories; `_CostExplorerMonthlyHistory` against a `botocore.stub.Stubber` `ce`
  client for 0/partial/full months and multi-service grouping with exclusion filtering;
  `AwsCostCheck.evaluate()` still computes the same month-to-date status as before (regression:
  history addition must not change the existing Ampel rule 4 outcome).
- **Unit (frontend):** each of the 4 chart renderers against synthetic v1.1 documents (empty,
  partial, well-formed) asserting trace count, x/y arrays, and reference-line `shapes`; diagram
  model builder against well-formed/missing-function/`null` documents; `pipeline_health.js` helpers
  null-safe by construction (existing convention: never throw on missing data).
- **Integration:** extend the existing moto-backed `PipelineHealthAggregator.aggregate()` end-to-end
  test to assert the v1.1 fields are present in the written JSON; a frontend integration check (via
  existing Vitest DOM setup, if present, or a manual smoke test) that tab activation mounts all 4
  charts + the diagram without throwing when given a real-shaped v1.1 fixture.
- **Edge cases:** insufficient-history environments (fresh dev, <5 invocations) must render partial
  charts (fewer bars/dots) rather than crash; a `null` document (load failure) must render the
  existing "not yet available" message and skip chart/diagram mounting entirely (no
  `Plotly.newPlot()` call against missing containers/data).
- **Coverage target:** >80% on all new/changed Python and frontend modules, matching project
  convention.

## Estimated monthly cloud cost

This feature adds **no new AWS resources** (no new Lambda, no new S3 prefix, no new IAM policy
beyond an already-granted `ce:GetCostAndUsage` action reused for a wider time window). The only
incremental cost is one extra Cost Explorer API call per pipeline-health Lambda invocation:

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| Cost Explorer API (`GetCostAndUsage`, 5-month per-service history) | $0.01/request | 1 extra call/week × 4.3 weeks × 2 environments (dev+prod) | ~$0.09 |
| Logs Insights query | unchanged (same query, one extra parsed field) | — | $0.00 |
| CloudWatch `GetMetricData` (API quota) | unchanged (no backend change) | — | $0.00 |
| S3 storage (slightly larger JSON, still a few KB) | ~$0.023/GB | negligible size delta | < $0.01 |
| **Total (new AWS components, combined dev+prod)** | | | **~$0.09–0.10/month** |

- **Cost drivers & cheaper alternatives:** the extra Cost Explorer call is the only new cost driver;
  reducing history-fetch frequency (e.g. only on every other run) would roughly halve it, but the
  absolute cost is negligible either way and the weekly cadence matches the pipeline's own schedule.
- **External / non-AWS costs:** none.
- **Budget check:** yes — this adds ~$0.10/month combined on top of FEATURE-012's already-accepted
  ~$0.06–0.10/month baseline, immaterial next to the project's existing ~$4–6/month combined
  dev+prod baseline (same budget-margin note FEATURE-012 already flagged).

## Success criteria

- [ ] `gold/pipeline_health/latest.json` declares `schema_version: "1.1"` and includes
      `recent_invocations` (execution success/duration, per function) and
      `monthly_cost_by_service` (AWS cost, 5 months) without changing any existing field's meaning.
- [ ] The Pipeline Health tab renders: the Medallion diagram, 4 KPI chart sections, and a
      threshold-rule caption per KPI, in both dev and prod, in all 5 locales.
- [ ] Each KPI's caption text matches the Ampel rule constants in `health_checks.py` verbatim (no
      drift between code and displayed text).
- [ ] A `null`/malformed pipeline-health document, or a single malformed KPI block, never breaks
      the tab, the diagram, or any of the other 3 KPI sections.
- [ ] Mobile layout: diagram and charts remain usable (no horizontal overflow, readable labels)
      at common mobile viewport widths.
- [ ] Tests pass; coverage holds >80% on new/changed code.
- [ ] `PIPELINE_HEALTH_LAYER.md` documents schema v1.1 and the new UI.
- [ ] Deployed to dev, manually verified, then promoted to prod (separate applies).
- [ ] Total new AWS cost stays under $0.20/month combined (verified against actual Cost Explorer
      data after 1 month in prod, same verification discipline as FEATURE-012).

## Open questions & risks

- **Question:** should `recent_invocations` be duplicated identically inside both
  `execution_success` and `execution_duration` details (this plan's default, favoring each block
  being self-sufficient), or should only one block carry the array and the other reference it by
  function name? Recommend duplication — the array is ≤5 small records, and independence avoids a
  frontend cross-lookup for what is otherwise a self-contained chart.
- **Question:** exact Plotly technique for the reference lines/bands (duration thresholds, quota
  80/95% bands) — plain `layout.shapes` vs. an extra "threshold" trace. Recommend `layout.shapes`
  (no extra legend entries) but confirm during implementation against `chart_theme.js` conventions.
- **Risk:** Cost Explorer's `GetCostAndUsage` has a documented rate limit and per-call cost; calling
  it twice per Lambda invocation (once for month-to-date status, once for 5-month history) doubles
  today's CE call volume for this Lambda. — *Mitigation:* both calls stay well under CE's
  rate limit at a weekly cadence; cost is quantified above and is negligible; consider merging into
  a single `GetCostAndUsage` call spanning 6 months (current + 5 prior) and deriving both the
  month-to-date total and the history from one response, if implementation finds this simpler than
  two calls — left as an implementation-time decision since it does not change the plan's shape.
- **Risk:** CloudWatch Logs Insights' per-query scanned-log-bytes cost could grow if `limit` is
  raised — not applicable here since the window stays at `EXECUTION_HISTORY_WINDOW = 5` (no
  increase), but flagged in case a future request asks for a longer history window.
- **Risk:** the diagram's per-node status lookup depends on matching `details.functions` keys
  (`{env}-idealista-collector` etc.) — a naming change in the Lambda functions (unlikely, but not
  impossible) would silently fall back to "unknown" status nodes rather than fail loudly.
  — *Mitigation:* tested explicitly as an edge case (Phase 5); documented in
  `PIPELINE_HEALTH_LAYER.md` as a coupling to keep in mind if Lambda names ever change.
- **Risk:** mobile layout for 4 charts + a diagram on one tab could feel long/heavy on small
  screens. — *Mitigation:* charts stack vertically (existing responsive convention), and the
  existing overall badge/sublights stay at the top as the "quick glance" entry point so a mobile
  user is not forced to scroll through detail to get the headline status.
- **Assumption:** dev and prod continue to have independent `gold/pipeline_health/latest.json`
  documents (per-environment Lambda names) — the diagram and charts operate on whichever
  environment's document the currently-loaded frontend fetches, exactly as FEATURE-012 already
  established; no cross-environment aggregation is introduced.

## Progress log

- **2026-08-18** — Plan drafted by `@architect` after reviewing FEATURE-012's plan, review, and
  current `health_checks.py`/frontend implementation; confirmed the Logs Insights query already
  selects `@timestamp` (just discarded) and the API-quota check already has usable history, so
  backend scope is narrowed to execution-history exposure + one new AWS-cost history adapter.
- **Implementation (tasks 13.1–13.14)** — All 14 tasks implemented by `@implementer` via strict
  TDD, one branch + commit per task, merged `--no-ff` into `main` in dependency order. Full test
  suites green throughout: 265 backend tests (`pytest src/etl -q`), 273 frontend tests
  (`npx vitest run`). `black`/`ruff` clean; `terraform fmt -check -recursive` and
  `terraform validate` clean for both `dev` and `prod`; `python dev/tools/validate_workflow.py`
  passes.
  - **Dev deployment & verification (task 13.14) — completed:** `terraform apply` in
    `infrastructure/environments/dev` deployed the updated `pipeline-health` Lambda code
    (schema v1.1); the Lambda was manually invoked and wrote a fresh
    `gold/pipeline_health/latest.json` confirmed via `aws s3 cp` to have `schema_version: "1.1"`,
    well-formed `recent_invocations` (5 entries per monitored function) and a 5-entry, oldest-first
    `monthly_cost_by_service` list. The dev CloudFront distribution was confirmed to still serve
    that JSON directly (review H2 regression check). The updated frontend bundle (`app.js`,
    `index.html`, `styles.css`, and the 6 new `src/**` modules) was synced to the dev assets bucket
    and the dev CloudFront cache invalidated; all 5 new JS modules and the new markup/CSS were
    confirmed reachable (HTTP 200) through the dev CloudFront URL.
  - **Prod promotion — intentionally not performed.** Per explicit instruction, prod deployment is
    held for a separate, explicitly-approved apply after dev verification is reviewed. Status stays
    🟡 *In progress* (not 🟢 *Complete*) until that promotion happens.
- **2026-11-13** — Status reconciled with reality: prod was promoted after the dev verification —
  `gold/pipeline_health/latest.json` reports `schema_version: "1.1"` in dev and prod, and the prod
  frontend bucket carries the detail-view assets. Marked 🟢 complete.
