# FEATURE-012 — Pipeline Health monitoring: overall traffic light (bronze/silver/gold, API quota, AWS cost)

**Status:** 🟢 Complete — deployed to dev and prod · **Effort:** L (~3.5–4 d) · **Priority:** Medium
**Branch root:** `feature/pipeline-health-monitoring` · **Created:** 2026-08-04 · **Updated:** 2026-08-04

> Authored by `@architect`. Reviewed by `@reviewer` (see `dev/reviews/REVIEW-FEATURE-012.md`).
> Implemented by `@implementer` from `dev/plans/technical/FEATURE-012-technical-plan.yaml`.

## Objective

Add a third dashboard tab, **"Pipeline Health"**, showing one overall red/yellow/green traffic
light for the medallion pipeline, composed of 4 independent sub-lights: Lambda execution success,
Lambda execution duration, Idealista API quota consumption, and AWS cost — all computed by a new,
small, scheduled Lambda and served as a static JSON, keeping the frontend 100% static.

## Context

The frontend is a static S3+CloudFront site with **no server and no AWS credentials in the
browser** (confirmed: only public `gold/aggregations/latest.json` is fetched, same-origin). None
of the health signals requested (Lambda invocation history, API request counts, AWS cost) are
things a static page can query directly — they must be computed server-side, on a schedule, and
published as another same-origin JSON file, exactly like the existing gold pipeline already does.

Verification findings that shape this design:

- **Lambda names** (dev/prod): `dev-idealista-collector` / `prod-idealista-collector` (bronze),
  `dev-silver-cleaner` / `prod-silver-cleaner` (silver), `dev-gold-aggregator` /
  `prod-gold-aggregator` (gold). Each environment's health Lambda monitors only its own
  environment's 3 functions, consistent with how dev/prod are otherwise kept independent.
- **LVW / PMV credential mapping is confirmed and asymmetric from what the request implied:**
  `LVW` (`leopold.walther@gmail.com`) is used for **sale**, `PMV` (`paulamarinvillar@gmail.com`)
  for **rent** (`bronze_collector.py`, confirmed by `test_bronze_collector.py`'s
  `test_collect_uses_per_operation_credentials`). The quota check is keyed by credential set, and
  the frontend/tooltip must present it as "LVW (sale)" / "PMV (rent)," not by any other
  interpretation.
- **No request-count/quota tracking exists today** — confirmed via full-repo search. This feature
  must add minimal instrumentation (a CloudWatch custom metric emitted from the bronze collector)
  before the quota sub-light can compute anything.
- **No custom CloudWatch metrics are published anywhere in the ETL code today** — only the
  standard `AWS/Lambda` `Errors`/`Duration`/`Invocations` metrics and one error alarm on the gold
  Lambda.
- **No Cost Explorer IAM permissions exist anywhere in the infrastructure today.**
- Existing budget baseline (`README.md`): **~$2–3/month per environment**, i.e. already close to
  the project's headline "< $5/month across both environments" target once dev + prod are summed.
  This feature — whose whole point is to warn about cost overruns — must itself add only cents.

## Dependencies

- **Needs:** FEATURE-011 — reuses the tab-navigation infrastructure (tab bar, `tab_state.js`,
  lazy-render-on-activation) built there; this feature only adds the 3rd tab and its data source.
- **Unblocks:** none currently planned.

## Design & patterns

### Ampel rule definitions (resolving the user's internal inconsistency)

The request said both "last **10** executions" and "**5/5** (or better) of the last executions" for
rule 1 — these are inconsistent as written. **Proposed, internally-consistent default (please
confirm/adjust in review):** standardise the execution-history window at **5** invocations for
rule 1, because rule 1's own YELLOW definition ("one of the 4 before the last") only makes sense
against a 5-invocation window, and rule 2 (duration) already independently specifies a 5-invocation
window — using 5 everywhere keeps the two execution-based rules consistent with each other. The
window size is a single named constant (`EXECUTION_HISTORY_WINDOW = 5`), so it is a one-line change
if the user prefers 10 after all (with GREEN then meaning "8+ of the last 10 succeeded," not
"5/5") — the resulting design keeps this swappable behind one Strategy class per rule.

1. **Execution success** (per Lambda function, worst of the 3 functions wins):
   - 🔴 RED — the most recent invocation failed.
   - 🟡 YELLOW — the most recent invocation succeeded, but at least 1 of the 4 invocations
     immediately before it failed.
   - 🟢 GREEN — all 5 of the last 5 invocations succeeded.
2. **Execution duration** (per Lambda function, last 5 invocations, worst of the 3 functions wins):
   - 🟢 GREEN — every invocation completed in under 5 minutes.
   - 🟡 YELLOW — at least one invocation took 5–10 minutes.
   - 🔴 RED — at least one invocation took over 10 minutes.
3. **API quota** (per credential set — LVW=sale, PMV=rent — worst of the 2 sets wins, evaluated
   over the **last 5 fully-completed calendar months**, excluding the current in-progress month to
   avoid a false-green bias from partial data):
   - 🟢 GREEN — every evaluated month used < 80 of the 100 requests/month quota.
   - 🟡 YELLOW — at least one month used ≥ 80%.
   - 🔴 RED — at least one month used ≥ 95%.
4. **AWS cost** (project-wide, month-to-date, excluding domain/registrar costs):
   - 🟢 GREEN — < $2/month.
   - 🟡 YELLOW — ≥ $2/month.
   - 🔴 RED — ≥ $5/month.

**Overall traffic light = the worst of the 4 sub-lights** (red beats yellow beats green) — a
single, explainable composition rule, computed by a plain function (`_worst_of(statuses)`), not a
class hierarchy — a Composite-pattern class was considered and rejected as unnecessary ceremony for
"take the max of 4 enum values."

### Backend: new Lambda, Strategy + Adapter per data source

Mirrors the exact orchestration shape already proven in `GoldAggregator`/`Aggregation`
(FEATURE-008): a narrow Protocol, one concrete Strategy class per health-check data source, and a
thin orchestrator that composes them — **Single Responsibility** (each check owns one data
source), **Open/Closed** (a 5th sub-light is one more class), **Dependency Inversion** (boto3
clients injected, never constructed inside the checks).

```python
class HealthCheck(Protocol):
    key: str
    def evaluate(self) -> HealthCheckResult: ...  # {status: 'green'|'yellow'|'red', details: dict}
```

- `ExecutionSuccessCheck(logs_client, function_names)` — Adapter around **CloudWatch Logs
  Insights** (`start_query`/`get_query_results`), not raw `AWS/Lambda` metrics: Logs Insights
  returns discrete per-invocation `REPORT`/error events, which is what "last 5 invocations,
  success/fail" needs; the standard `Errors`/`Invocations` metrics are calendar-period aggregates
  and can't answer "was invocation N-1 specifically a failure."
- `ExecutionDurationCheck(logs_client, function_names)` — same Logs Insights adapter, reads
  `@duration` from the same `REPORT` events (one query per function covers both rules 1 and 2 —
  implemented as one shared query helper, two Strategy classes consuming its result).
- `ApiQuotaCheck(cloudwatch_client, credential_sets=('LVW', 'PMV'))` — Adapter around
  `GetMetricData` on the new custom metric (see instrumentation below), summed per calendar month.
- `AwsCostCheck(cost_explorer_client, excluded_services=('Amazon Registrar', 'Amazon Route 53
  Domains'))` — Adapter around `ce.GetCostAndUsage`, grouped by `SERVICE`, month-to-date, with an
  explicit service-name exclusion list (Cost Explorer has no native "exclude domain registration"
  filter; tag-based filtering was considered but domain/registrar charges are not reliably taggable
  resources, so a service-name exclusion is the pragmatic choice — documented as a risk below).
- `PipelineHealthAggregator` (mirrors `GoldAggregator`): runs the 4 checks, composes the overall
  status, writes `gold/pipeline_health/latest.json` via the **existing `ObjectStore` Adapter** —
  no new storage abstraction needed.

### Instrumentation: minimal API-quota tracking (new, since none exists)

`bronze_collector.py`'s `BronzeCollector` gains one call per operation after each collection run:
`cloudwatch.put_metric_data(Namespace='VlcRealEstate/Idealista', MetricName='ApiRequests',
Dimensions=[{'Name': 'CredentialSet', 'Value': 'LVW'|'PMV'}], Value=<pages_fetched>)`. This is
injected via a new narrow `MetricsPublisher` Protocol (Adapter pattern, same shape as the existing
`ObjectStore`/`SecretsProvider`/`Notifier` Protocols in `common/`) so the collector stays testable
with an in-memory fake, and boto3 stays out of the orchestration class per the FEATURE-008
convention. Only 2 custom metrics are created (one per credential set) — within CloudWatch's 10
free custom metrics/month, so this instrumentation adds **$0**.

### Infrastructure (Terraform)

New module `infrastructure/modules/lambda_pipeline_health/`, structured exactly like
`lambda_gold/` (`main.tf`, `variables.tf`, `outputs.tf`, `fileset()`-based deployment package
including `common/`, dedicated IAM role, 30-day log retention). Instantiated once per environment
in `environments/{dev,prod}/main.tf`, after the `gold_aggregator` module. **Independent
EventBridge schedule** (not part of the Step Functions pipeline orchestration — this Lambda
observes the pipeline, it does not participate in it), e.g. `cron(0 13 ? * SUN *)` — 15 minutes
after gold's `12:45` run, so it can report on that week's just-completed pipeline execution.

**IAM policy (least privilege):**
- `logs:StartQuery`, `logs:GetQueryResults`, `logs:StopQuery`, `logs:DescribeLogGroups` — scoped
  by resource ARN to the 3 pipeline Lambdas' log groups in this environment only.
- `cloudwatch:GetMetricData` — CloudWatch does not support resource-level scoping for this action;
  accepted as `Resource: "*"`, documented as a CloudWatch API limitation, not a design gap.
- `ce:GetCostAndUsage` — Cost Explorer actions are also `Resource: "*"`-only by AWS design (no
  ARNs exist for cost data); this is the one new IAM surface with no way to scope further.
- `s3:PutObject` — scoped to `gold/pipeline_health/*` only (reuses the existing bucket + prefix
  convention).
- `cloudwatch:PutMetricData` (bronze Lambda's role, not the health Lambda's) — scoped via a
  `cloudwatch:namespace` condition key to `VlcRealEstate/Idealista` only.
- Optional `sns:Publish` on the existing notification topic, to alert immediately when the overall
  status flips to red (small, high-value addition — flagged as optional Phase 5 work, not required
  for MVP).

### Frontend

- New tab `pipeline-health` appended to `tab_state.js`'s `TAB_IDS` (built in FEATURE-011).
- New `PipelineHealthDataSource` (mirrors the existing `DataSource` Adapter exactly, second
  instance, fetching `/gold/pipeline_health/latest.json`, same `schema_version` guard pattern).
- New `frontend/src/pipeline_health.js` — pure functions: `overallBadgeLabel(status, locale)`,
  `subLightDetails(check, locale)`, no Plotly needed (this tab is badges + small text tables, not
  charts) — keeps the tab cheap to build and avoids inventing chart types for what is fundamentally
  a status dashboard.
- New CSS tokens for green/yellow/red badges in `styles.css`'s existing design-token block
  (`:root`, `[data-theme="dark"]`), consistent with the rest of the dark/light system.
- If the health JSON fails to load (e.g., health Lambda hasn't run yet in a fresh environment), the
  tab shows a neutral "not yet available" state — must never break the other two tabs (independent
  `DataSource` instance, independent error handling, matching the project's existing
  load-state-machine pattern in `dashboard_state.js`).

## Approach

### Phase 1 — Backend: API-quota instrumentation
- [x] `MetricsPublisher` Protocol + `CloudWatchMetricsPublisher` adapter + in-memory fake in
      `src/etl/common/metrics_publisher.py`, unit-tested like the existing `common/` adapters.
- [x] `BronzeCollector` gains an injected `MetricsPublisher`; emits one `put_metric` call per
      operation per run. Existing bronze tests updated to inject a fake publisher; behaviour
      (collected data) is unchanged.
- [x] Terraform: add `cloudwatch:PutMetricData` (namespace-scoped) to the bronze Lambda's IAM
      policy.

### Phase 2 — Backend: health-check strategies (TDD, one branch per check)
- [x] `HealthCheckResult` dataclass + `HealthCheck` Protocol in
      `src/etl/pipeline_health/health_checks.py` (new package).
- [x] `ExecutionSuccessCheck` + `ExecutionDurationCheck` — shared Logs Insights query helper,
      unit-tested against a stubbed `logs` client (moto's Logs Insights support is limited/absent
      in some versions — see Open Questions; fall back to `botocore.stub.Stubber` if needed).
- [x] `ApiQuotaCheck` — unit-tested against a stubbed/moto `cloudwatch` client.
- [x] `AwsCostCheck` — unit-tested against a stubbed `ce` client (moto's Cost Explorer support must
      be verified first — see Open Questions).
- [x] `_worst_of(statuses)` pure function + unit tests (all 3×3×3×3 combinations is overkill;
      cover the 4 boundary cases: all green, one red, one yellow no red, mixed).

### Phase 3 — Backend: orchestrator + Lambda handler
- [x] `PipelineHealthAggregator` class (mirrors `GoldAggregator`), writes
      `gold/pipeline_health/latest.json` via the existing `ObjectStore` Adapter.
- [x] Thin `pipeline_health_lambda.py` handler — Factory wire-up (boto3 clients → adapters →
      aggregator), matching the `gold_aggregation_lambda.py` shape.
- [x] Integration test: moto-backed S3 write, stubbed CloudWatch/Cost Explorer clients, asserts
      the full JSON shape and overall-status composition end to end.

### Phase 4 — Infrastructure
- [x] `infrastructure/modules/lambda_pipeline_health/` (main.tf, variables.tf, outputs.tf),
      mirroring `lambda_gold/`'s structure and least-privilege IAM pattern.
- [x] Wire the module into `environments/dev/main.tf` first, `terraform plan`/`apply` to dev only.
- [ ] Manually invoke the dev health Lambda, download `gold/pipeline_health/latest.json`, verify
      shape and plausible statuses (dev pipeline has few real invocations yet — verify the checks
      degrade gracefully with a short history, e.g. "green with only 2 of 5 slots filled" rather
      than crashing). *(Deployment-only — requires a real AWS apply; documented as a step-by-step
      instruction in PIPELINE_HEALTH_LAYER.md, not yet executed.)*
- [ ] Only after dev verification: wire into `environments/prod/main.tf`, separate `terraform
      apply` (FEATURE-006/010 promotion pattern). *(Deployment-only — not yet executed.)*

### Phase 5 — Frontend
- [x] `PipelineHealthDataSource` + tests (mirrors `data_source.test.js`).
- [x] `frontend/src/pipeline_health.js` + tests — pure formatting/labelling helpers.
- [x] Add `pipeline-health` to `tab_state.js`'s `TAB_IDS`, wire the 3rd tab panel in `index.html`
      + `app.js` (lazy-loaded independently of the FEATURE-011 charts — a health-JSON fetch
      failure must not affect the other two tabs).
- [x] Overall badge + 4 sub-light rows (status + one-line detail, e.g. "Execution success: 🟢 —
      gold: 5/5, silver: 5/5, bronze: 5/5") + i18n keys in all 5 locales.
- [x] Styles: green/yellow/red badge tokens in `styles.css`, dark/light variants.

### Phase 6 — Docs & optional alerting
- [x] New `documentation/PIPELINE_HEALTH_LAYER.md` (mirrors `DATA_GOLD_LAYER.md`'s structure):
      JSON schema, the 4 ampel rules verbatim, design decisions, sample output.
- [ ] Optional: wire `AwsCostCheck`/`ExecutionSuccessCheck` red status to the existing SNS topic
      for an immediate alert (separate small task, not required for tab MVP — deferred, review L1).

## Files

- **Create:**
  - `src/etl/common/metrics_publisher.py`
  - `src/etl/pipeline_health/__init__.py`, `health_checks.py`, `pipeline_health_aggregator.py`,
    `pipeline_health_lambda.py`
  - `src/etl/pipeline_health/tests/*` (one test file per check + aggregator + lambda)
  - `infrastructure/modules/lambda_pipeline_health/main.tf`, `variables.tf`, `outputs.tf`
  - `frontend/src/pipeline_health_data_source.js`, `frontend/src/pipeline_health.js`
  - `frontend/tests/pipeline_health_data_source.test.js`, `pipeline_health.test.js`
  - `documentation/PIPELINE_HEALTH_LAYER.md`
- **Change:**
  - `src/etl/data_collection/bronze_collector.py` — inject `MetricsPublisher`, emit quota metric
  - `src/etl/data_collection/tests/test_bronze_collector.py` — inject fake publisher
  - `infrastructure/environments/dev/main.tf`, `prod/main.tf` — instantiate the new module
  - `frontend/src/tab_state.js` — add `pipeline-health` tab id
  - `frontend/index.html`, `app.js`, `styles.css`, `i18n.js` — 3rd tab wiring, badge styles, keys
- **Tests:** as listed above, plus updated bronze collector tests.

## Test strategy

- **Unit (Python):** each `HealthCheck` strategy tested in isolation against a stubbed client for
  all 3 statuses (green/yellow/red boundary cases) plus a "not enough history yet" edge case;
  `_worst_of` boundary cases; `MetricsPublisher` fake records calls without hitting AWS.
- **Integration (Python):** moto-backed `PipelineHealthAggregator.aggregate()` end-to-end test
  (S3 write real via moto; CloudWatch Logs Insights / Cost Explorer via stubs if moto coverage is
  insufficient — to be confirmed in Phase 2, see Open Questions).
- **Unit (JS/Vitest):** `pipeline_health.js` formatting helpers; `PipelineHealthDataSource` schema
  guard and fetch-failure handling (must not throw uncaught — tab shows "not yet available").
- **Manual:** dev Lambda invocation + visual tab check before prod promotion (FEATURE-010 pattern);
  confirm a fresh environment with <5 invocations doesn't crash the tab.

## Estimated monthly cloud cost

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| `pipeline-health-aggregator` Lambda | $0.20/1M requests + $0.0000166667/GB-s | 4 invocations/month (weekly), <30s, 256 MB | < $0.01 |
| CloudWatch Logs Insights queries | $0.005/GB scanned | ~8 queries/month (2 per function × 3 functions, batched), KB-scale logs | < $0.01 |
| CloudWatch `GetMetricData` | $0.01/1,000 metrics beyond 1M/month free tier | ~10 calls/month | $0.00 |
| CloudWatch custom metric (`PutMetricData`, bronze) | first 10 metrics free, then $0.30/metric/month | 2 metrics (LVW, PMV) — within free tier | $0.00 |
| Cost Explorer `GetCostAndUsage` | $0.01/API request (not in any free tier) | 4 calls/month (weekly) | ~$0.04 |
| S3 storage (new small JSON) | ~$0.023/GB | a few KB | < $0.01 |
| **Total (new AWS components, per environment)** | | | **~$0.06–0.10/month** |

- **Cost drivers & cheaper alternatives:** Cost Explorer API calls dominate the incremental cost
  (only paid item in the list); reducing frequency from weekly to monthly would roughly quarter it,
  but weekly matches the pipeline cadence and keeps the cost signal timely — the absolute cost is
  negligible either way.
- **External / non-AWS costs:** none.
- **Budget check:** yes, at the margin — the project's existing baseline is already ~$2–3/month
  *per environment* (~$4–6/month combined dev+prod, which is itself already at or slightly over
  the README's headline "< $5/month across both environments" framing — an existing
  inconsistency in the README's cost narrative, not something this feature causes). This feature
  adds only ~$0.10–0.20/month combined across both environments, which is immaterial next to the
  existing baseline, but it does not create headroom either. Flagged for the user's awareness, not
  a blocker.

## Success criteria

- [x] "Pipeline Health" tab shows one overall traffic light + 4 sub-lights with a one-line detail
      each, refreshed weekly, for both dev and prod once deployed
- [x] All 4 ampel rules implemented exactly as specified above (with the 5-invocation-window
      assumption confirmed or adjusted in review)
- [x] API-quota sub-light correctly labels LVW as sale and PMV as rent
- [x] AWS-cost sub-light excludes Route 53/domain registrar costs
- [ ] New Lambda deployed to dev, manually verified, then promoted to prod (separate applies)
- [x] Health-JSON fetch failure never breaks the Trend Analysis or Data Basis tabs
- [x] Tests pass, coverage holds >80% on new code
- [x] `PIPELINE_HEALTH_LAYER.md` documents the schema and the 4 rules verbatim
- [ ] Total new AWS cost stays under $0.20/month combined (verified against actual Cost Explorer
      data after 1 month in prod)

## Open questions & risks

- **Question (blocking, needs user confirmation):** the request text says both "10 executions"
  and "5/5" for rule 1 — this plan standardises on a 5-invocation window for internal consistency
  with rule 2 and rule 1's own YELLOW definition. Please confirm, or specify that GREEN should
  instead require "8+ of the last 10" (keeping YELLOW/RED as written against the last 5).
- **Question:** should the search-radius/API-quota labelling surface the literal secret names
  (`lvw`/`pmv`) or only the human labels ("sale credentials"/"rent credentials")? Recommend the
  latter — no operational secret-naming details on a public dashboard.
- **Risk:** moto's support for CloudWatch Logs Insights (`start_query`/`get_query_results`) and
  Cost Explorer (`ce`) may be partial or absent depending on the pinned moto version in
  `requirements-dev.txt`. — *Mitigation:* verify moto coverage at the start of Phase 2; fall back
  to `botocore.stub.Stubber` for whichever client(s) moto doesn't support, without changing the
  adapter's public interface.
- **Risk:** Cost Explorer excludes domain/registrar costs via a service-name list, not a tag
  filter, because domain registration charges are not reliably taggable. — *Mitigation:* document
  the exact excluded service name(s) in `PIPELINE_HEALTH_LAYER.md` and revisit if AWS changes how
  Route 53 Domains costs are categorised.
- **Risk:** a brand-new environment (or one right after this feature ships) has fewer than 5
  historical invocations to evaluate — rules must degrade gracefully (e.g., treat "green" as "all
  available invocations succeeded" when history < 5) rather than crash or report a misleading red.
  — *Mitigation:* explicit "insufficient history" handling tested in Phase 2, surfaced in the
  detail text rather than hidden.
- **Assumption:** the health Lambda runs independently of the Step Functions pipeline orchestrator
  (it observes, it does not participate) — confirmed acceptable since it has its own EventBridge
  schedule and does not need `create_schedule = false` like the 3 pipeline Lambdas do.

## Progress log

- **2026-08-04** — Plan drafted by `@architect` after codebase verification (Lambda names, LVW/PMV
  mapping, absence of quota/cost instrumentation, existing IAM/Terraform patterns, budget baseline).
- **2026-08-11** — `@implementer` completed all 12 technical-plan tasks (12.1–12.12) on
  `feature/pipeline-health-monitoring`: `MetricsPublisher` Protocol/adapter + bronze
  attempt-granularity instrumentation (review H1 fix); the 4 Ampel-rule `HealthCheck` Strategy
  classes (`ExecutionSuccessCheck`, `ExecutionDurationCheck`, `ApiQuotaCheck`, `AwsCostCheck`) with
  bounded-polling Logs Insights, Cost Explorer `us-east-1` client region (review M1), and explicit
  credential-global quota scoping (review M3); `PipelineHealthAggregator` +
  `pipeline_health_lambda.py` writing `gold/pipeline_health/latest.json`;
  `infrastructure/modules/lambda_pipeline_health/` wired into `environments/dev/main.tf` and
  `environments/prod/main.tf`; `infrastructure/modules/frontend` extended with a second
  `ordered_cache_behavior`/bucket-policy statement for `gold/pipeline_health/*` (review H2 fix);
  frontend `PipelineHealthDataSource` + `pipeline_health.js` + the 3rd dashboard tab (badges,
  styles, i18n); and `documentation/PIPELINE_HEALTH_LAYER.md` documenting the schema, rules, quota
  scope, Cost Explorer exclusions, insufficient-history behavior, CloudFront path, and manual
  dev-to-prod deployment/validation steps. Terraform has **not** yet been applied to either
  environment — the dev manual invocation/verification and prod promotion steps remain open and
  are documented as instructions only.
- **2026-11-13** — Status reconciled with reality: `dev-pipeline-health` and `prod-pipeline-health`
  are deployed with their weekly schedules, and both environments serve a current
  `gold/pipeline_health/latest.json`. The "not yet applied" remark above is historical. Marked 🟢
  complete. The optional SNS red-status alert stays an unscheduled idea.
