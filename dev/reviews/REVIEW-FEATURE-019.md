# Review — FEATURE-019: Move the Idealista credentials from Secrets Manager to SSM Parameter Store

**Reviewer:** `/reviewer` · **Date:** 2026-11-14 · **Plan:** [FEATURE-019](../plans/FEATURE-019-ssm-parameter-store-credentials.md)
**Verdict:** ⚠️ Changes Recommended

## Summary

The plan is sound: the `SecretsProvider` Protocol makes this a one-adapter change, the order is
additive, and nothing is deleted before prod has run on the new path. No blocker. Four should-fix
items tighten it: grant `kms:Decrypt` explicitly instead of discovering the need in dev (M1), keep
the existing variable names to shrink the cut-over (M2), make the migration prove that the copied
values are identical (M3), and deny parameter values to the CI roles account-wide (M4). One
statement in the plan is factually wrong but its conclusion holds (M5).

## Strengths

- ✅ Reuses the existing abstraction. `BronzeCollector` depends on the Protocol only, so the new
  adapter is a drop-in (Liskov) and the collector tests double as a regression guard.
- ✅ No dual-read mode or feature flag. Terraform ships code and configuration per environment in
  one apply, so each environment switches atomically.
- ✅ Keeps the FEATURE-018 guarantee: values never enter Terraform, the state or CI.
- ✅ Rollback is cheap until the cleanup phase — the secrets stay untouched until prod was green.
- ✅ Honest about the payback (~$19/year) and quota-aware (one dev pipeline run).

## Findings

### 🟡 M1 — Grant `kms:Decrypt` explicitly instead of testing for it in dev

- **Problem:** The plan treats "does the permissions boundary require an explicit `kms:Decrypt`?"
  as an open risk to be settled by the dev smoke run. Whether a key-policy grant to the whole
  account passes a permissions boundary without an identity-side allow is not something to rely
  on. If the answer is "required", the cut-over PR fails in dev, the boundary needs another local
  apply and the PR has to be reworked.
- **Recommendation:** In 19.2 add `kms:Decrypt` to the boundary and in 19.4 to the bronze role,
  with `resources = ["*"]` scoped by two conditions: `kms:ViaService = ssm.<region>.amazonaws.com`
  and `kms:EncryptionContext:PARAMETER_ARN` matching the project's parameter ARNs. This needs no
  `aws_kms_alias` data source — the plan role has no KMS read permission and a data source would
  break the CI plan. The grant is harmless if it turns out to be redundant.
- **Evidence:** `infrastructure/shared/github-oidc/main.tf` contains no `kms:` or `ssm:` action
  today; `local.terraform_read_actions` has no KMS reads.
- **Effort:** S

### 🟡 M2 — Keep `SECRET_NAME_LVW` / `SECRET_NAME_PMV`; drop only the ARN variables

- **Problem:** The plan renames the environment variables to `CREDENTIALS_PARAM_*` and replaces
  all four module variables. That widens the riskiest PR (handler, handler tests, module, both
  environments) for a cosmetic gain.
- **Recommendation:** Keep the environment variables and the module variables `secret_name_lvw` /
  `secret_name_pmv` (update their descriptions). Remove `secret_arn_lvw` / `secret_arn_pmv` and
  build the parameter ARN inside `modules/lambda_bronze`. The handler diff shrinks to the injected
  provider class; `test_idealista_collector.py` keeps its environment fixture.
- **Note:** parameter names start with `/`, so the ARN is
  `arn:aws:ssm:<region>:<account>:parameter` + name — no extra slash.
- **Effort:** S (saves time)

### 🟡 M3 — The migration must prove the values are identical, without showing them

- **Problem:** The plan verifies the copy with `describe-parameters`, which proves existence only.
  A truncated or empty value would surface on the first pipeline run — in prod that is the Sunday
  run.
- **Recommendation:** The script compares a SHA-256 of the secret value and of the decrypted
  parameter value and prints only `match` / `MISMATCH`. Pass the value with
  `--value file:///dev/stdin` (never as a command-line argument, which is visible in the process
  list), no `set -x`, no `--debug`. Place it in the existing `dev/tools/` directory;
  `infrastructure/scripts/` does not exist.
- **Effort:** S

### 🟡 M4 — Deny parameter values to the CI roles on `*`, not on the project path

- **Problem:** The plan scopes the new deny to `parameter/vlc-market-pulse/*/idealista/*`. The
  existing `DenySecretValues` statements use `resources = ["*"]`, and the account also holds the
  sibling project's parameters.
- **Recommendation:** Add `ssm:GetParameter`, `ssm:GetParameters`, `ssm:GetParametersByPath`,
  `ssm:GetParameterHistory`, `ssm:PutParameter` and `ssm:DeleteParameter(s)` to a deny on `*` in
  the plan and apply roles. Neither role needs SSM at all. Add matrix cases for read and write.
- **Effort:** S

### 🟡 M5 — Wrong reason for keeping the parameters out of Terraform

- **Problem:** The plan says the pinned provider has no write-only value. The locked provider
  (5.100.0) does expose `aws_ssm_parameter.value_wo` (checked with `terraform providers schema`).
- **Why the decision still stands:** managing the parameter in Terraform makes the provider read
  it on every refresh, so the CI plan and apply roles would need `ssm:GetParameter` plus decrypt
  on the credential — exactly what M4 denies. Unmanaged parameters are the right call; correct
  the stated reason in the plan.
- **Effort:** S (wording)

### 🟢 L1 — Prod parameters must exist before the cut-over merges

- **Suggestion:** Make "parameters exist and match in dev **and prod**" an explicit gate of 19.4.
- **Why:** after 19.4 is on `main`, any prod deploy — also one triggered for an unrelated feature —
  carries the cut-over to prod.

### 🟢 L2 — Keep the cleanup PR out of the bronze smoke paths

- **Suggestion:** 19.6 must not touch `src/etl/data_collection/` or `modules/lambda_bronze/`.
- **Why:** those prefixes switch the smoke job to the full pipeline and would spend a second dev
  run. After 19.4 nothing there references Secrets Manager any more.

### 🟢 L3 — Reject non-object payloads in the adapter

- **Suggestion:** `SsmParameterProvider.get_secret` raises `SecretError` when the decoded JSON is
  not an object, in addition to missing parameter and invalid JSON.
- **Why:** a clear error instead of a `TypeError` deep in the collector.

## Alternatives considered

- **Terraform-managed parameter with `value_wo` placeholder** — Terraform would own existence and
  tags. Trade-off: CI needs read and decrypt on the credential for every plan. Verdict: rejected
  (see M5).
- **Stay on Secrets Manager** — zero work, $19/year. Verdict: legitimate; the owner chose to
  proceed. The lasting benefit is one credential mechanism across both projects.
- **Parameters and Secrets Lambda extension / caching** — fewer API calls. Verdict: pointless at
  two reads per run.

## Risks

| Risk | Likelihood | Impact | Severity | Mitigation |
| --- | --- | --- | --- | --- |
| Bronze role cannot decrypt (boundary / KMS) | Low after M1 | Med | 🟡 | Explicit grant; credentials are read before the first API call, so a failed dev run spends no quota |
| Copied value differs from the secret | Low | High (lost prod snapshot) | 🟡 | Hash comparison (M3); dev runs first |
| Prod picks up the cut-over before its parameters exist | Low | High | 🟡 | Gate in 19.4 (L1) |
| Prod fails on its first Sunday run | Low | High | 🟡 | Apply prod early in the week, simulate the role, secrets kept for rollback |
| A value is printed during migration | Low | High | 🟡 | stdin-only script, owner runs it locally, never CI or an AI agent |
| Second dev pipeline run spent | Low | Low | 🟢 | L2; 19.4 is the only task touching bronze paths |
| Parameter missing in a rebuilt environment | Low | Med | 🟢 | README setup step; `SecretError` plus SNS alert |

## Effort check

- **Plan estimate:** S–M (~7 h)
- **Reviewer estimate:** M (~8 h) — confidence Medium
- **Why it differs / hidden complexity:** KMS grant and wider matrix (+0.5 h), hash verification
  (+0.25 h), doc sweep across six files (+0.75 h); M2 saves ~0.5 h. Calendar time is at least two
  weeks: one scheduled prod run before cleanup, then the 7-day recovery window.

## Reuse & conflicts

- **Reuse:** `src/etl/common/secrets_provider.py` — Protocol, `SecretError`, in-memory fake.
- **Reuse:** `infrastructure/shared/github-oidc/simulate_apply_role.py` and
  `dev/tools/iam_action_inventory.sh --check` — both must stay green after 19.2 and 19.7.
- **Reuse:** the FEATURE-018 smoke job — it runs the full dev pipeline exactly when bronze paths
  change, which is the integration test for 19.4.
- **Conflict / coordinate with:** any prod deploy between 19.4 and its verification (L1).

## Approval criteria

- **Blockers (must fix):** none
- **Recommended:** M1, M2, M3, M4, M5 — all folded into the technical plan
- **Optional:** L1, L2, L3 — folded in as acceptance criteria

## Next step

`/implementer FEATURE-019`, starting with 19.1–19.3 (independent of each other). 19.2 and 19.7 need
a local apply of `shared/github-oidc`; 19.3 is run by the owner only.

---

### Post-implementation notes
*Filled in after the task ships.*

- **Worked well:** <…>
- **Missed in review:** <…>
- **Estimated vs. actual:** <X> vs. <Y>
