# FEATURE-007 — Step Functions orchestration of the bronze → silver → gold pipeline

**Status:** 🟢 Complete — deployed to dev and prod · **Effort:** M (~12–16 h) · **Priority:** Medium
**Branch root:** `feature/step-functions-orchestration` · **Created:** 2026-06-06 · **Updated:** 2026-06-06

> Authored by `@architect`. Reviewed by `@reviewer` (see `dev/reviews/REVIEW-FEATURE-007.md`).
> Implemented by `@implementer` from `dev/plans/technical/FEATURE-007-technical-plan.yaml`.

## Objective

Replace the three independent, time-based EventBridge schedules that trigger the collector,
silver, and gold Lambdas with a single **AWS Step Functions** state machine that runs them in a
dependency-aware sequence (bronze → silver → gold), with built-in retry, error catching, and a
single failure alert. One weekly trigger drives the whole pipeline instead of three disconnected
cron rules.

## Context

The medallion pipeline is currently wired as **three independent scheduled Lambdas**:

| Stage | Module | Trigger today | Source |
|---|---|---|---|
| Bronze collector | `lambda_bronze` | `cron(0 12 ? * SUN *)` | [infrastructure/modules/lambda_bronze/main.tf](infrastructure/modules/lambda_bronze/main.tf) |
| Silver cleaning | `lambda_silver` | `cron(30 12 ? * SUN *)` | FEATURE-003 |
| Gold aggregation | `lambda_gold` | `cron(45 12 ? * SUN *)` | FEATURE-004 |

The stages are coupled by **wall-clock guesswork**, not by data dependencies. Problems with the
current design:

- **No failure propagation.** If the silver Lambda fails at 12:30, the gold Lambda still fires at
  12:45 and aggregates a stale/partial silver layer — producing a wrong `latest.json` for the
  dashboard.
- **No retry/backoff at the orchestration level.** A transient failure in any stage just fails that
  run; the next stage runs anyway on its own timer.
- **Fragile timing.** The 15-minute gaps are arbitrary. If the collector ever takes longer than
  30 min, silver runs on incomplete bronze data.
- **No single source of truth for "did the weekly run succeed?"** — three separate CloudWatch
  alarms, no end-to-end view.

FEATURE-003 and FEATURE-004 each documented "no Step Functions" as a **cost-driven** decision when
the pipeline was a single stage. Now that there are **three dependent stages**, that trade-off is
revisited: Step Functions Standard Workflows cost ~$0 at this volume (see cost section) while
removing the timing fragility entirely. This feature supersedes that earlier note.

**Existing patterns to follow:**

- Reuse the existing SNS topic (`idealista_notifications`) for the single end-to-end failure alert.
- Keep all three Lambda modules unchanged in their *code*; only their **EventBridge trigger** moves
  into the state machine (the per-Lambda schedules are removed).
- Terraform module conventions from `lambda_bronze` / `lambda_silver` (IAM least privilege,
  region-aware variables, CloudWatch log group, tags).

## Dependencies

- **Needs:** FEATURE-003 — `lambda_silver` module exists ✅ done
- **Needs:** FEATURE-004 — `lambda_gold` module exists (tasks 4.2–4.4 pending) ⏳
- **Coordinates with:** FEATURE-006 — prod promotion. **Important:** 006 currently plans to wire the
  silver + gold Lambdas into prod with their *independent EventBridge schedules*. With this feature,
  prod should instead wire the **state machine**. See "Open questions & risks". Recommended order:
  land FEATURE-007 in dev first, then have FEATURE-006 promote the orchestrated pipeline (not the
  three separate crons).
- **Unblocks:** A reliable weekly `gold/aggregations/latest.json` for FEATURE-005 (frontend).

## Approach

The state machine is a **Standard Workflow** (not Express): the stages are low-frequency and
long-ish (Lambda invocations up to 15 min), Standard gives a visual execution history and durable
retry semantics, and the cost is negligible at weekly cadence.

```
EventBridge Scheduler  cron(0 12 ? * SUN *)   (single weekly trigger)
        │ StartExecution
        ▼
┌────────────────────────────────────────────────────────────┐
│  State machine: vlc-medallion-pipeline                      │
│                                                            │
│   [Collect Bronze]  ── Lambda invoke (lambda_bronze)        │
│        │ Retry 2x backoff · Catch → Notify                  │
│        ▼                                                    │
│   [Clean Silver]    ── Lambda invoke (lambda_silver)        │
│        │ Retry 2x backoff · Catch → Notify                  │
│        ▼                                                    │
│   [Aggregate Gold]  ── Lambda invoke (lambda_gold)          │
│        │ Retry 2x backoff · Catch → Notify                  │
│        ▼                                                    │
│   [Success]                                                 │
│                                                            │
│   [Notify Failure]  ── SNS Publish (idealista_notifications)│
│        └─ then Fail                                         │
└────────────────────────────────────────────────────────────┘
```

### Phase 1 — State machine definition + Terraform module
- [ ] New module `infrastructure/modules/pipeline_orchestrator/` holding the Amazon States Language
      (ASL) definition (as a `templatefile` so Lambda ARNs are injected, not hardcoded) and the
      `aws_sfn_state_machine` resource (type `STANDARD`).
- [ ] Three `Task` states (`arn:aws:states:::lambda:invoke`) for bronze → silver → gold, each with a
      `Retry` block (e.g. `IntervalSeconds: 30`, `MaxAttempts: 2`, `BackoffRate: 2.0`) and a `Catch`
      routing to a shared `NotifyFailure` state.
- [ ] `NotifyFailure` state (`arn:aws:states:::sns:publish`) → existing SNS topic, then `Fail`.
- [ ] Pass `test_mode` into the bronze task input so dev still collects 1 page/operation (mirrors the
      current `lambda_bronze` `test_mode` behaviour).

### Phase 2 — IAM, scheduling, and trigger migration
- [ ] State-machine IAM role: `lambda:InvokeFunction` on exactly the three Lambda ARNs, `sns:Publish`
      on the topic, and CloudWatch Logs for the state machine — nothing else.
- [ ] EventBridge Scheduler rule `cron(0 12 ? * SUN *)` with a `StartExecution` target on the state
      machine (replaces the three independent rules).
- [ ] **Remove** the per-Lambda EventBridge rules/targets/permissions from `lambda_bronze`,
      `lambda_silver`, `lambda_gold` (or gate them behind a module flag
      `create_schedule = false`) so the schedule lives in exactly one place.
- [ ] CloudWatch log group `/aws/vendedlogs/states/{env}-medallion-pipeline` (or standard log group)
      with 30-day retention; enable `ALL`-level logging on the state machine.

### Phase 3 — Wire dev, validate, document
- [ ] Instantiate `module "pipeline_orchestrator"` in `infrastructure/environments/dev/main.tf`,
      passing the three Lambda ARNs and the SNS topic ARN. Prod wiring is **deferred to FEATURE-006**
      (mirrors the silver/gold deferral).
- [ ] `terraform fmt -check` + `terraform validate` pass in `dev`.
- [ ] Document the orchestration in `documentation/DATA_PROCESSING_LAYER.md` (state machine diagram,
      retry/catch behaviour, how to re-run a failed execution from the console).

## Files

- **Create:** `infrastructure/modules/pipeline_orchestrator/main.tf` — `aws_sfn_state_machine`, IAM role/policy, EventBridge Scheduler rule + target, CloudWatch log group
- **Create:** `infrastructure/modules/pipeline_orchestrator/state_machine.asl.json` — ASL definition rendered via `templatefile` with injected Lambda/SNS ARNs
- **Create:** `infrastructure/modules/pipeline_orchestrator/variables.tf` — Lambda ARNs, SNS topic ARN, environment, region, `test_mode`
- **Create:** `infrastructure/modules/pipeline_orchestrator/outputs.tf` — state machine ARN/name
- **Change:** `infrastructure/modules/lambda_bronze/main.tf` — remove (or flag off via `create_schedule`) the standalone EventBridge rule/target/permission
- **Change:** `infrastructure/modules/lambda_silver/main.tf` — same
- **Change:** `infrastructure/modules/lambda_gold/main.tf` — same (once FEATURE-004 lands)
- **Change:** `infrastructure/environments/dev/main.tf` — instantiate `pipeline_orchestrator`; pass Lambda + SNS ARNs; set module schedule flags to false
- **Change:** `documentation/DATA_PROCESSING_LAYER.md` — orchestration architecture + runbook
- **Tests:** `infrastructure/modules/pipeline_orchestrator/` — `terraform validate`; an ASL JSON syntax/schema check (e.g. a small Python test asserting the rendered definition has the three states + retry/catch wiring)

## Test strategy

- **Unit (ASL):** a Python test renders/loads the ASL definition and asserts: three `Task` states in
  order, each with a `Retry` and a `Catch` → `NotifyFailure`, and `NotifyFailure` publishes to SNS
  then transitions to `Fail`. Network-free, deterministic.
- **Terraform:** `terraform validate` + `terraform fmt -check` pass for the module and the dev env.
  Confirm no Lambda ARNs are hardcoded (all via `templatefile` vars).
- **Integration (manual, dev):** trigger one execution; confirm bronze → silver → gold run in order,
  the execution shows green in the Step Functions console, and a forced silver failure routes to
  `NotifyFailure` (SNS alert) and stops gold from running.
- **Regression:** confirm the three old EventBridge rules no longer exist after apply (exactly one
  schedule remains).

## Estimated monthly cloud cost

The pipeline runs **weekly** (~4.33 executions/month). A single execution traverses ~6–8 state
transitions (3 task states + retries/choice/terminal states).

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| **Step Functions (Standard)** | $0.025 / 1,000 state transitions | ~8 transitions × 4.33 runs ≈ 35 | ~$0.001 |
| **EventBridge Scheduler** | $1.00 / million invocations | 4.33 StartExecution calls | <$0.01 |
| **CloudWatch Logs (state machine)** | ~$0.57 / GB ingest + $0.03 / GB stored | a few KB/run, 30-day retention | ~$0.01 |
| **Lambda (bronze/silver/gold)** | unchanged | same invocations, now orchestrated | $0.00 (no change) |
| **SNS** | unchanged | reuses existing topic | $0.00 (no change) |
| **Total (new AWS components)** | | | **~$0.02/month** |

- **Cost drivers & cheaper alternatives:** State transitions dominate but are trivially cheap at
  weekly cadence. **Express Workflows** would be even cheaper per run but bill by duration × memory
  and cap at 5 min — unsuitable because the bronze Lambda can run up to 15 min. Standard is the right
  choice and effectively free here. This **reverses** the earlier "no Step Functions for cost"
  decision in FEATURE-003/004: at this volume the cost difference vs. plain EventBridge is < $0.02/mo.
- **External / non-AWS costs:** none.
- **Budget check:** Yes — adds ~$0.02/month, far within the project's < $5/month target. Running it
  in prod as well (via FEATURE-006) roughly doubles it to ~$0.04/month.

## Success criteria

- [ ] A single Step Functions Standard state machine runs bronze → silver → gold in order
- [ ] Each stage has retry (2 attempts, exponential backoff) and a catch routing to one SNS alert
- [ ] A failure in any stage **stops** downstream stages (gold never runs on a failed silver)
- [ ] Exactly **one** EventBridge schedule remains (the StartExecution trigger); the three per-Lambda
      schedules are gone
- [ ] State-machine IAM role is least-privilege (invoke only the three Lambdas + publish to the topic)
- [ ] No Lambda/SNS ARNs hardcoded — all injected via `templatefile`/variables
- [ ] ASL unit test + `terraform validate` + `terraform fmt -check` pass
- [ ] `workflow-consistency` CI check passes
- [ ] `documentation/DATA_PROCESSING_LAYER.md` documents the state machine + re-run runbook
- [ ] Prod wiring intentionally deferred to FEATURE-006 (not done here)

## Open questions & risks

- **Question — coordination with FEATURE-006:** 006 currently wires the silver + gold Lambdas into
  prod with their independent schedules. Should 006 be updated to wire the **state machine** instead
  (recommended), or should 007 own all prod wiring? *Recommendation:* 007 lands in dev only; 006 is
  amended to promote the orchestrated pipeline (state machine) rather than three separate crons.
- **Question — schedule flag vs. removal:** remove the per-Lambda EventBridge rules outright, or keep
  them behind a `create_schedule = false` flag for backwards-compatibility? *Recommendation:* flag,
  so the Lambda modules stay independently usable/testable.
- **Risk — FEATURE-004 not yet complete.** `lambda_gold` (tasks 4.2–4.4) must exist before the gold
  task state can reference its ARN. *Mitigation:* implement Phase 1–2 against bronze + silver first;
  add the gold task state once 004 lands, or treat gold wiring as the final task gated on 004.
- **Risk — `terraform apply` removes live schedules and adds a state machine.** Applying in dev will
  delete the three EventBridge rules and create the state machine in one change. *Mitigation:* review
  the plan carefully; the actual apply is a manual step; verify the single schedule post-apply.
- **Risk — reversing the documented "no Step Functions" decision.** FEATURE-003/004 explicitly chose
  against it on cost grounds. *Mitigation:* this plan documents that the trade-off changed (three
  dependent stages now; cost ~$0.02/mo) so the reversal is explicit and owned.
- **Assumption:** all three Lambdas accept a simple JSON event and are safe to invoke synchronously
  from a Step Functions `Task` state (silver/gold already accept optional `event` keys; bronze
  accepts `test_mode`).

## Progress log

- **2026-06-06** — Plan authored. Replaces the three independent EventBridge schedules with a single
  Standard state machine (bronze → silver → gold) with retry/catch/SNS. Coordinates with FEATURE-006
  (prod promotion should wire the state machine, not the separate crons). Gold task state gated on
  FEATURE-004 completion.
- **2026-11-13** — Status reconciled with reality: all six tasks are `done` in the technical plan and
  both `dev-medallion-pipeline` and `prod-medallion-pipeline` exist in AWS, each with a single
  `*-medallion-pipeline-weekly` schedule and no per-Lambda schedules. Marked 🟢 complete.
