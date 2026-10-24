# Review — FEATURE-018: Working, secure Terraform deploys from GitHub Actions

**Reviewer:** `/reviewer` · **Date:** 2026-10-24 · **Plan:** [FEATURE-018](../plans/FEATURE-018-ci-terraform-deploy.md)
**Verdict:** ⚠️ Changes Recommended

## Summary

The direction is right: split plan and apply roles, gate prod, and take the Idealista credentials out
of Terraform. Phase 1 alone already unbreaks the frontend deploy and removes a real hazard. Two
gaps would hurt in production. The permissions boundary as sketched would starve the
pipeline-health Lambda and the Step Functions role at runtime. The plan role could read plaintext
API keys from state if it lands before the secrets migration. Both are fixable by ordering and
an explicit inventory. Several inputs the plan wants to configure in GitHub already have defaults
in code, which shrinks Phase 3.

## Strengths

- ✅ Root-cause analysis is accurate and evidenced: immutable OIDC subjects, a least-privilege role
  vs. a full apply, wrong tfvars names, and missing GitHub secrets.
- ✅ Taking credentials out of Terraform removes the "empty secret overwrites prod keys" failure mode
  entirely, rather than papering over it with GitHub secrets.
- ✅ Keeping `shared/github-oidc` local-apply only is the correct anti-self-escalation boundary.
- ✅ Phase 1 is independently shippable and delivers user value (frontend deploy) on its own.
- ✅ Cost analysis is honest (~$0) and the public-repo log exposure is recognised.

## Findings

### 🔴 H1 — Permissions boundary is incomplete and would break Lambdas outside the smoke path

- **Problem:** The boundary is described as "Lambda/SFN/S3/Logs/SNS/Secrets read/CloudWatch only".
  The workload roles actually use `ce:GetCostAndUsage`, `cloudwatch:GetMetricData`,
  `logs:StartQuery`/`GetQueryResults`/`StopQuery` (pipeline health),
  `logs:CreateLogDelivery`/`GetLogDelivery`/`UpdateLogDelivery`/`DeleteLogDelivery`/`ListLogDeliveries`/`PutResourcePolicy`/`DescribeResourcePolicies`/`DescribeLogGroups`
  (SFN logging), `lambda:InvokeFunction` (SFN), `states:StartExecution` (Scheduler) and
  `cloudwatch:PutMetricData` (bronze).
- **Impact:** The planned dev smoke test runs the state machine only. `pipeline_health` runs on its
  own schedule (`cron(0 13 ? * SUN *)`), so a boundary that is too tight breaks the health tab in
  prod silently, one week later.
- **Recommendation:** Derive the boundary mechanically from the union of all
  `aws_iam_role_policy` actions in `infrastructure/modules/**` (inventory command in the technical
  plan). Add an acceptance check that diffs the boundary against that union. Extend the smoke step
  to also invoke `{env}-pipeline-health` once and require `statusCode == 200`.
- **Evidence:** `grep -hoE '"(ce|cloudwatch|lambda|logs|states|…):[A-Za-z*]+"' infrastructure/modules/*/main.tf`.

### 🔴 H2 — Plan role must not be able to read credentials from state (ordering + object versions)

- **Problem:** Today both env states contain `aws_secretsmanager_secret_version.secret_string` in
  plaintext, and the state bucket has **versioning enabled**. A plan role with
  `s3:GetObject` created before task 1.2 is applied in *both* envs lets any PR workflow read the
  keys. After 1.2, older object versions still contain them.
- **Impact:** Credential exposure to PR-triggered jobs, whose logs are public.
- **Recommendation:** Hard ordering: the plan and apply roles are created only after 1.2 is applied
  to dev **and** prod. Grant `s3:GetObject` only, never `s3:GetObjectVersion`, scoped to the two env
  state keys, not `/*`. Narrow the existing `github-actions-deploy` state read the same way (it
  currently reads every state in the bucket, including `shared/*`).

### 🟡 M1 — Apply role guard rails need explicit deny-list

- **Problem:** "IAM writes conditioned on the boundary" leaves well-known bypasses open:
  `iam:DeleteRolePermissionsBoundary`, `iam:PutRolePermissionsBoundary` with another policy,
  `iam:CreatePolicyVersion`/`SetDefaultPolicyVersion` on the boundary itself, `iam:CreatePolicy` +
  `AttachRolePolicy` of a new admin policy, and `iam:PassRole` to non-project services.
- **Recommendation:** Explicit `Deny` statements for boundary deletion and replacement, any write on
  the boundary policy and on `role/github-actions-*`, `iam:CreatePolicy`, `AttachRolePolicy` (the
  project uses inline policies only), `secretsmanager:GetSecretValue`/`PutSecretValue`, and state
  writes outside the env keys. `iam:PassRole` is limited with `iam:PassedToService` ∈
  {lambda, states, scheduler}. Each deny becomes a row in the simulation matrix.
- **Effort:** S

### 🟡 M2 — Smoke test can exhaust the shared Idealista quota

- **Problem:** The quota is 100 requests/month (`API_QUOTA_MONTHLY_REQUESTS`). prod uses ~13/week and
  dev ~2/week, about 65 per month. Dev and prod use the same two Idealista accounts (see
  `modules/secrets` descriptions). At 2 requests per merge, ~15 merges a month push usage into the
  red threshold (95) that the health tab alarms on.
- **Recommendation:** Run the state-machine smoke only when the merge touches
  `src/etl/data_collection/**`, `infrastructure/modules/lambda_bronze/**` or
  `infrastructure/modules/pipeline_orchestrator/**`. For other merges, invoke silver/gold/health
  directly (no API calls). Document the budget.
- **Effort:** S

### 🟡 M3 — Public logs: raw `terraform plan` stdout leaks the full diff

- **Problem:** The plan restricts the *job summary* to addresses and actions, but the `terraform plan`
  stdout (and the `setup-terraform` wrapper, which echoes stdout as step outputs) still prints every
  attribute to the public log. That includes state machine definitions, env vars and IAM policies.
  `terraform show -json` also contains unredacted sensitive values, so it must never be uploaded or
  echoed whole.
- **Recommendation:** `terraform_wrapper: false`. Run `terraform plan -out=tfplan -no-color > /dev/null`
  (keep stderr for errors). Build the summary with
  `terraform show -json tfplan | jq '[.resource_changes[] | {address, actions: .change.actions}]'`.
  Never use `actions/upload-artifact` on plan files.
- **Effort:** S

### 🟡 M4 — Pin third-party actions to commit SHAs

- **Problem:** The apply role effectively controls the project's AWS footprint. `hashicorp/setup-terraform@v3`,
  `aws-actions/configure-aws-credentials@v4` etc. are mutable tags, a known supply-chain vector.
- **Recommendation:** Pin every `uses:` in the deploy and validate workflows to a full SHA with the tag
  as a comment. Cheap, and it removes the most likely real-world compromise path.
- **Effort:** S

### 🟡 M5 — Phase 3.1 is mostly unnecessary: inputs already have defaults

- **Problem:** `notification_email` and `pandas_layer_arn` have `default`s in both
  `environments/*/variables.tf`. `prod/terraform.tfvars` is empty, and `dev/terraform.tfvars` repeats
  the default layer ARN. The email is already public in the repo.
- **Recommendation:** Drop the `TF_VAR_*` GitHub variables. Delete the redundant `dev/terraform.tfvars`
  line so local and CI runs use identical inputs (no `-var-file` at all). Keep `sensitive = true` on
  `notification_email` for log hygiene only, and note that it does not make the value secret.
- **Effort:** S (negative effort, it removes work)

### 🟢 L1 — `removed` block placement and version floor

- **Suggestion:** `removed { lifecycle { destroy = false } }` needs Terraform ≥ 1.7. Bump
  `required_version` in `modules/secrets` (currently `>= 1.2`). Put the blocks in the root modules
  with `from = module.idealista_secrets.aws_secretsmanager_secret_version.…`, so the intent is visible
  where the apply happens. The acceptance check is the plan text "will no longer be managed by
  Terraform" with 0 to destroy.
- **Why:** It avoids a surprise `destroy`, and the recovery window does not protect secret
  *versions*.

### 🟢 L2 — Remove leftovers from the old role

- **Suggestion:** Once `deploy-lambda.yml` is deleted, drop `LambdaUpdateFunctionCode` and the DynamoDB
  lock statement (no table exists) from `github-actions-deploy`.
- **Why:** Least privilege, and less to reason about.

### 🟢 L3 — Lint workflows

- **Suggestion:** Run `actionlint` locally per task (or add it to `workflow-consistency`).
- **Why:** The workflows reference reusable workflows and environment gates, where typos only show at
  runtime.

## Alternatives considered

- **Code-only deploys from CI, infra stays local** — much smaller, minimal privileges. Trade-off:
  infra changes like PR #44 still need a local apply. Verdict: rejected by the user (2026-10-24), who
  wants fully automated applies.
- **Single broad apply role without a boundary** — saves ~3 h. Trade-off: a compromised workflow or
  action can mint an admin role and take over the account. Verdict: keep the boundary (H1/M1), since
  the cost is one-off.
- **Terraform Cloud / Spacelift** — managed plan/apply with approvals. Trade-off: new SaaS, account
  and state migration. Verdict: overkill for one maintainer.

## Risks

| Risk | Likelihood | Impact | Severity | Mitigation |
| --- | --- | --- | --- | --- |
| Boundary too tight breaks a Lambda | Med | High | 🔴 | H1: derive from inventory, and the smoke test also invokes pipeline-health |
| Plan role reads keys from state | Med | High | 🔴 | H2: ordering, no `GetObjectVersion`, key-scoped |
| Apply-role policy too tight → half-applied dev | High | Low | 🟡 | Iterate on dev. Applies are idempotent; re-run after a fix |
| `removed` misread → secret versions destroyed | Low | High | 🟡 | L1: explicit "0 to destroy" acceptance; local apply with a human reading the plan |
| Quota exhausted by smoke runs | Med | Med | 🟡 | M2: path-gated smoke |
| Public log leakage | Med | Low | 🟡 | M3: silent plan, summary from JSON addresses only |
| Supply-chain compromise of an action | Low | High | 🟡 | M4: SHA pinning |

## Effort check

- **Plan estimate:** M (~10–12h)
- **Reviewer estimate:** M–L (~15–16h), confidence Med
- **Why it differs / hidden complexity:** iterating the apply-role policy on real applies (the first
  apply typically surfaces 3–5 missing actions, e.g. `tag:*`, `Describe*` on CloudFront OAC, ACM in
  us-east-1), the deny matrix, and the boundary rollout to 6 roles across 2 envs. M5 saves ~1 h.

## Reuse & conflicts

- **Reuse:** `infrastructure/shared/github-oidc` already has the OIDC provider and the pattern for
  scoped policy documents. Extend it, do not create a new stack.
- **Reuse:** `terraform-validate.yml` already has the PR-comment script. Add the plan summary there.
- **Conflict / coordinate with:** PR #44 is merged, but prod is **not yet applied** (local apply
  pending by the owner). Task 18.2's prod apply will include those changes, so the owner should apply
  PR #44 first to keep diffs readable.

## Approval criteria

- **Blockers (must fix):** H1, H2. Both are folded into the technical plan's task order and
  acceptance criteria.
- **Recommended:** M1–M5 (all folded in).
- **Optional:** L1–L3 (L1 and L2 folded in, L3 as a per-task check).

## Next step

The technical plan below incorporates all findings. Proceed with `/implementer FEATURE-018`, starting
at 18.1 (independently shippable).

---

### Post-implementation notes
*Filled in after the task ships.*

- **Worked well:** <…>
- **Missed in review:** <…>
- **Estimated vs. actual:** <X> vs. <Y>
