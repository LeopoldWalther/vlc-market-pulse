# FEATURE-018 — Working, secure Terraform deploys from GitHub Actions

**Status:** 🔵 Planned · **Effort:** M (~10–12h) · **Priority:** High
**Branch root:** `feature/ci-terraform-deploy` · **Created:** 2026-10-24 · **Updated:** 2026-10-24

> Authored by `/architect`. Reviewed by `/reviewer` (see `dev/reviews/REVIEW-FEATURE-018.md`).
> Implemented by `/implementer` from `dev/plans/technical/FEATURE-018-technical-plan.yaml`.

## Objective

Deploy dev and prod infrastructure and code from GitHub Actions with no local `terraform apply`.
Every PR gets a read-only `terraform plan`. Merges to `main` apply to dev automatically. Prod is
applied on demand, only after manual approval. Idealista API credentials leave Terraform and
GitHub entirely.

## Context

Verified against account `063096286047` and the GitHub repository on 2026-10-24:

- **`deploy-lambda.yml` has never succeeded.** Its first run (2026-10-24, run `40109988697`) failed
  at `Configure AWS Credentials` with `Not authorized to perform sts:AssumeRoleWithWebIdentity`.
- **Root cause 1: OIDC subject format.** The repo uses GitHub's immutable subject claims
  (`gh api repos/LeopoldWalther/vlc-market-pulse/actions/oidc/customization/sub` →
  `sub_claim_prefix: repo:LeopoldWalther@32717417/vlc-market-pulse@1123272680`). The trust policy
  of `github-actions-deploy` (`infrastructure/shared/github-oidc/main.tf`) only allows the legacy
  `repo:LeopoldWalther/vlc-market-pulse:…` form. **`deploy-frontend.yml` is broken for the same
  reason.** It last succeeded on 2026-07-17, before the repo rename on 2026-09-10 (`a7f9905`).
- **Root cause 2: permissions.** The role is least-privilege on purpose (frontend S3 sync, CloudFront
  invalidation, `lambda:UpdateFunctionCode` on the collector only, state read). It cannot run the
  full `terraform apply` that `deploy-lambda.yml` performs (IAM, Step Functions, Scheduler, Lambda
  config, …).
- **Latent hazard: credentials.** `deploy-lambda.yml` writes `lvw_api_key`/`pmv_api_key`… to
  `secrets.tfvars`, but the root modules declare `idealista_api_key_lvw` etc. None of the referenced
  GitHub secrets exist, at either repo or environment level. Once auth worked, the run would at
  best have failed on missing variables. With the names fixed, it would have written **empty
  credentials** into Secrets Manager (`modules/secrets` owns the `aws_secretsmanager_secret_version`).
- **Missing non-secret inputs.** `terraform.tfvars` (`notification_email`, `pandas_layer_arn`, …) is
  gitignored, so CI has no source for these values.
- **No gate on prod.** GitHub environments `dev` and `prod` exist but have no protection rules. The
  repo is **public**, so required reviewers are available for free, and all Actions logs are public.
- `terraform-validate.yml` already runs `fmt` + `validate` (with `-backend=false`) on PRs and posts a
  comment. This feature extends it with a real plan.

## Dependencies

- **Needs:** none. Builds on the existing `shared/github-oidc` stack and the S3-native-lock backend.
- **Unblocks:** every future infra change (e.g. the FEATURE-019+ pipeline work) can ship via PR →
  merge → approve, with no local credentials. It also fixes the broken frontend deploy.

## Design

No Python is involved. The "design" is the IAM and workflow architecture, which follows the same
SOLID ideas the codebase uses:

| Principle | Applied as |
|---|---|
| **Single Responsibility** | Three roles, one job each: `github-actions-plan` (read-only plan on PRs), `github-actions-apply` (apply per environment), `github-actions-deploy` (existing: frontend sync + invalidation; unchanged scope). |
| **Interface Segregation / least privilege** | The plan role gets AWS `ReadOnlyAccess`-style *describe/list/get* actions plus state read, and **no** `secretsmanager:GetSecretValue`. The apply role is scoped by resource-name prefix (`dev-*`, `prod-*`, project buckets) rather than `*`. |
| **Dependency Inversion** | Workflows depend on role ARNs from GitHub *variables* (`AWS_PLAN_ROLE_ARN`, `AWS_APPLY_ROLE_ARN`), not hard-coded ARNs. Non-secret inputs come from GitHub environment variables as `TF_VAR_*`. |
| **Open/Closed** | One reusable workflow (`_terraform.yml`, `workflow_call`) takes `environment` + `mode` (`plan`/`apply`). The PR, dev-on-merge and prod-dispatch entry points are thin callers. Adding a `staging` env means adding a caller, not editing the core. |
| **Template Method** (workflow-level) | The reusable workflow fixes the skeleton: checkout → assume role → init → plan → (apply), and callers only choose mode and environment. |

**Trust model (who may assume what):**

| Role | Allowed `sub` (both legacy *and* immutable prefix) | Used by |
|---|---|---|
| `github-actions-plan` | `…:pull_request`, `…:ref:refs/heads/main` | PR plan, pre-apply plan |
| `github-actions-apply` | `…:environment:dev`, `…:environment:prod` | apply jobs (env-gated) |
| `github-actions-deploy` | `…:environment:dev`, `…:environment:prod` | `deploy-frontend.yml` |

The prod environment requires the owner as reviewer and only allows deployments from `main`.
Fork PRs never receive OIDC tokens, so the plan role is unreachable from forks.

**Privilege-escalation guard.** The apply role can create and modify IAM roles, so without a guard
it could mint itself an admin role. Mitigation: a customer-managed **permissions boundary**
`vlc-workload-boundary` (Lambda/SFN/S3/Logs/SNS/Secrets read/CloudWatch only). The apply role may
call `iam:CreateRole`/`PutRolePolicy`/`AttachRolePolicy` only on `arn:aws:iam::…:role/dev-*` and
`…/prod-*`, and only with `iam:PermissionsBoundary` = that boundary. It cannot touch `github-actions-*`
roles or the boundary policy. All 6 workload roles gain `permissions_boundary`. The `shared/github-oidc`
stack stays **local-apply only**, because CI must never manage its own trust.

**Credentials.** `modules/secrets` keeps the two `aws_secretsmanager_secret` resources and drops the
`_version` resources via `removed { lifecycle { destroy = false } }` (Terraform ≥ 1.7), so the
current values stay in AWS untouched. Values are rotated by hand with
`aws secretsmanager put-secret-value`, documented in the README. The `idealista_api_*` variables
and `secrets.tfvars` are deleted.

## Approach

### Phase 1 — Unblock auth and remove the credential hazard
- [ ] **1.1 Trust policy accepts immutable subjects.** Add `github_owner_id` (`32717417`) and
  `github_repo_id` (`1123272680`) variables. Build the allowed `sub` list from both prefixes. Apply
  `shared/github-oidc` locally. *Test:* `terraform plan` shows only the trust-policy diff, and
  `deploy-frontend.yml` for dev succeeds.
- [ ] **1.2 Decouple Idealista credentials from Terraform.** `removed` blocks for both secret
  versions. Delete the 4 `idealista_api_*` variables in `modules/secrets` and both environments.
  *Test:* `terraform plan` (dev, prod) shows "will be removed from state, not destroyed" and nothing
  else. After apply, `aws secretsmanager get-secret-value` metadata shows an unchanged `VersionId`.
- [ ] **1.3 Retire the broken `deploy-lambda.yml`** (superseded by Phase 3), and drop the pointless
  `secrets.tfvars` step from `deploy-frontend.yml`.

### Phase 2 — Roles and boundary (`shared/github-oidc`, local apply)
- [ ] **2.1 Permissions boundary** `vlc-workload-boundary` + `permissions_boundary` on the 6 workload
  roles (`{env}-idealista-collector-lambda-role`, `-silver-cleaner-`, `-gold-aggregator-`,
  `-pipeline-health-`, `-medallion-pipeline-sfn-role`, `-medallion-pipeline-scheduler-role`).
  *Test:* dev apply, then one dev pipeline run succeeds end to end (proves the boundary is not too
  tight).
- [ ] **2.2 `github-actions-plan` role.** Read-only describe/list/get for every resource type in
  `environments/*` (Lambda, IAM, SFN, Scheduler, Events, Logs, S3, CloudFront, ACM, Route53, SNS,
  CloudWatch, Secrets Manager *DescribeSecret* only), plus state `GetObject`/`ListBucket`. Plans run
  with `-lock=false`. *Test:* a local `aws sts assume-role`-equivalent dry run is not possible for
  OIDC, so verify via the Phase 3 PR plan job.
- [ ] **2.3 `github-actions-apply` role.** Write actions scoped by name prefix, `iam:PassRole` only
  for `dev-*`/`prod-*` roles to `lambda`/`states`/`scheduler`, IAM writes conditioned on the boundary,
  and state read/write and lockfile on the env prefixes only. *Test:* the IAM Policy Simulator
  (`aws iam simulate-principal-policy`) confirms Allow for representative actions and Deny for
  `iam:CreateRole` without the boundary, for `github-actions-*` and for `GetSecretValue`.

### Phase 3 — Workflows
- [ ] **3.1 GitHub configuration** (documented, scripted with `gh`): repo variables
  `AWS_PLAN_ROLE_ARN`, `AWS_APPLY_ROLE_ARN`. Per-environment variables `TF_VAR_notification_email`
  and `TF_VAR_pandas_layer_arn`. On `prod`, required reviewer = owner and deployment branches =
  `main` only. Mark `notification_email` as `sensitive = true` so it never prints in public logs.
- [ ] **3.2 Reusable `_terraform.yml`** (`workflow_call`: `environment`, `mode`). Concurrency group
  `terraform-${environment}` (no cancel). Steps: init → `plan -out` → on `apply` mode,
  `apply tfplan`. The plan is written to the **job summary** as resource addresses + action counts
  only (`terraform show -json` filtered with `jq`). No attribute values, because logs are public.
- [ ] **3.3 Entry points.** Extend `terraform-validate.yml`: a PR plan for dev and prod (plan role,
  posted as a PR comment). New `terraform-deploy.yml`: `push: main` (paths `infrastructure/**`,
  `src/etl/**`) → apply dev, and `workflow_dispatch` → apply prod behind the `prod` approval gate.
- [ ] **3.4 Post-apply smoke test (dev only):** start one dev state-machine execution (test mode,
  2 API calls) and fail the job if it does not reach `SUCCEEDED`. There is no prod smoke run,
  because the Sunday schedule is the prod check and a manual run would spend real API quota.

### Phase 4 — Docs
- [ ] **4.1** README / `documentation/` section "Deploying": PR → plan, merge → dev, dispatch +
  approve → prod. One-off local steps (`shared/github-oidc` apply, secret rotation command).
  Update `CLAUDE.md` CI/CD table.

## Files

- **Change:** `infrastructure/shared/github-oidc/{main,variables,outputs}.tf`: immutable subjects,
  plan/apply roles, boundary policy, outputs for the new ARNs.
- **Change:** `infrastructure/modules/secrets/main.tf`, `variables.tf`: `removed` blocks, drop vars.
- **Change:** `infrastructure/environments/{dev,prod}/{main,variables}.tf`: drop credential vars,
  `notification_email` sensitive, pass boundary ARN to the modules.
- **Change:** `infrastructure/modules/{lambda_bronze,lambda_silver,lambda_gold,lambda_pipeline_health,pipeline_orchestrator}/{main,variables}.tf`:
  `permissions_boundary`.
- **Create:** `.github/workflows/_terraform.yml`, `.github/workflows/terraform-deploy.yml`.
- **Change:** `.github/workflows/terraform-validate.yml` (PR plan), `deploy-frontend.yml` (drop
  secrets step).
- **Delete:** `.github/workflows/deploy-lambda.yml`.
- **Docs:** `README.md`, `documentation/DATA_COLLECTION_LAYER.md` (deployment section), `CLAUDE.md`.

## Test strategy

There is no Python, so coverage is unaffected. Each slice is verified against real AWS/GitHub:

- **Static:** `terraform fmt`/`validate` for all three roots; `actionlint` on the workflows (new CI
  step, or locally).
- **Policy:** `aws iam simulate-principal-policy` matrix for the apply role: Allow on
  `lambda:UpdateFunctionConfiguration` for `prod-idealista-collector`; Deny on
  `iam:CreateRole` without the boundary, `iam:UpdateAssumeRolePolicy` on `github-actions-deploy`,
  `secretsmanager:GetSecretValue`, `s3:DeleteBucket` on the state bucket. The plan role is Deny on
  every write action sampled.
- **End-to-end:** (1) a PR touching `infrastructure/` gets a plan comment for dev + prod. (2) The
  merge auto-applies dev and the smoke execution succeeds. (3) A prod dispatch waits for approval,
  then applies with "No changes" (prod should already match after the local apply of PR #44).
  (4) `deploy-frontend.yml` succeeds for dev.
- **Negative:** a dispatch from a non-`main` branch to `prod` is rejected by the environment branch
  rule.

## Estimated monthly cloud cost

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| IAM roles, policies, boundary, OIDC | free | 2 new roles, 1 managed policy | $0 |
| S3 state requests | $0.0004 / 1k GET | ~50 plan/apply runs | <$0.01 |
| Dev smoke execution (SFN + Lambda) | Standard SFN $0.025 / 1k transitions | ~10 extra runs | <$0.01 |
| GitHub Actions minutes | free for public repos | — | $0 |
| **Total (new AWS components)** | | | **~$0/month** |

- **Cost drivers & cheaper alternatives:** none material. The dev smoke run also spends 2 Idealista
  API calls per merge, so it is limited to merges that touch `src/etl/**` or `infrastructure/**`.
- **External / non-AWS costs:** none.
- **Budget check:** yes, well within the < $5/month target.

## Success criteria

- [ ] A PR touching `infrastructure/` shows a dev + prod plan summary, produced with the read-only role.
- [ ] Merging to `main` applies dev with no human step, and the dev smoke execution succeeds.
- [ ] A prod apply only runs from `main` after owner approval.
- [ ] `deploy-frontend.yml` works again for dev and prod.
- [ ] No Idealista credential exists in Terraform state, tfvars, or GitHub. Secret `VersionId`s are
  unchanged by the migration.
- [ ] The apply role cannot create a role without the boundary, or modify `github-actions-*`
  (verified by simulation).
- [ ] No sensitive values (email, secret content) appear in public Actions logs or PR comments.
- [ ] Docs describe the deploy flow and the one-off local steps.

## Open questions & risks

- **Question:** Should dev auto-apply on every merge, or also be dispatch-only? *Proposal:* auto,
  because dev is the safety net. Confirm in review.
- **Risk:** the apply-role policy is either too tight (applies fail mid-way) or too loose. *Mitigation:*
  build it from the resource-type inventory (27 types in `environments/*`). Iterate on dev first,
  where a failed apply is harmless. Keep the simulation matrix in the docs.
- **Risk:** the permissions boundary is too tight and breaks a Lambda at runtime. *Mitigation:* the
  boundary mirrors the union of the current inline policies, the dev smoke run catches regressions,
  and prod is applied only after dev succeeds.
- **Risk:** public logs leak plan details (account ID, ARNs). *Mitigation:* summaries show addresses
  and actions only, and `notification_email` is sensitive. The account ID is already public in
  this repo's docs.
- **Risk:** the `removed` block misfires and destroys secret versions. *Mitigation:* review the plan
  output explicitly for "will no longer be managed" vs "destroy" before apply. Secret
  `recovery_window_in_days = 7` is an additional net.
- **Assumption:** GitHub keeps immutable subjects enabled. Both prefixes stay in the trust policy
  so a toggle in either direction keeps working.
- **Assumption:** `pandas_layer_arn` is a stable, non-secret ARN, safe as a GitHub environment variable.

## Progress log

- **2026-10-24** — Plan created after the first `deploy-lambda.yml` run failed during the rollout of
  PR #44 (single pipeline email). Prod still needs a local `terraform apply` for PR #44.
