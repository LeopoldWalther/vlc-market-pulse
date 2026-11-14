# FEATURE-019 — Move the Idealista credentials from Secrets Manager to SSM Parameter Store

**Status:** 🔵 Planned · **Effort:** S–M (~7 h) · **Priority:** Low
**Branch root:** `feature/ssm-parameter-store-credentials` · **Created:** 2026-11-14 · **Updated:** 2026-11-14

> Authored by `/architect`. Reviewed by `/reviewer` (see `dev/reviews/REVIEW-FEATURE-019.md`).
> Implemented by `/implementer` from `dev/plans/technical/FEATURE-019-technical-plan.yaml`.

## Objective

Store the two Idealista credential sets (LVW, PMV) per environment as `SecureString` parameters in
SSM Parameter Store (standard tier) instead of AWS Secrets Manager. This removes the only fixed
monthly charge of the credential handling (~$1.60/month) without weakening the guarantees from
FEATURE-018: no credential value in Terraform, in the state, or readable by CI.

## Context

- **Today:** `infrastructure/modules/secrets` creates two Secrets Manager secrets per environment
  (`{env}/idealista/lvw-api-credentials`, `{env}/idealista/pmv-api-credentials`). Terraform owns
  only the containers; the owner sets the values with `aws secretsmanager put-secret-value`
  (README, "Rotating Idealista credentials").
- **Consumer:** only the bronze Lambda. `idealista_listings_collector.py` reads `SECRET_NAME_LVW` /
  `SECRET_NAME_PMV` and injects `SecretsManagerProvider()` into `BronzeCollector`, which calls
  `get_secret(name)` and expects a mapping with `api_key` and `api_secret`.
- **Abstraction already exists:** `src/etl/common/secrets_provider.py` defines the
  `SecretsProvider` Protocol with the `SecretsManagerProvider` adapter and an in-memory fake. The
  collector depends on the Protocol only, so the change is one new adapter plus wiring.
- **IAM that names Secrets Manager:**
  - `infrastructure/modules/lambda_bronze/main.tf` — role policy `lambda_secrets`
    (`secretsmanager:GetSecretValue` on the two ARNs).
  - `infrastructure/shared/github-oidc/main.tf` — workload permissions boundary
    (`IdealistaSecretsRead`), plan role (metadata reads, `DenySecretValues`), apply role
    (`SecretMetadata`, `DenySecretValues`), plus the matrix in `simulate_apply_role.py`.
- **Precedent:** the sibling project already keeps its API key in Parameter Store
  (`/vlc-market-oracle/{env}/zenrows-api-key`).
- **Why the cost is fixed:** Secrets Manager bills $0.40 per secret per month regardless of use;
  4 secrets = $1.60. Standard-tier parameters are free, and the pipeline reads them about twice a
  week per environment.

## Dependencies

- **Needs:** FEATURE-018 (complete) — the CI roles, the permissions boundary and the "values never
  in Terraform" rule this plan has to preserve.
- **Unblocks:** nothing.

## Design & patterns

- **Adapter (new):** `SsmParameterProvider` in `common/secrets_provider.py` wraps the boto3 SSM
  client behind the existing `SecretsProvider` Protocol. `get_secret(name)` calls
  `GetParameter(Name=name, WithDecryption=True)` and JSON-decodes the value; failures raise the
  existing `SecretError`. The client is private and constructor-injected (tests pass a moto client).
- **Polymorphism / Liskov:** the new adapter, the old one and `InMemorySecretsProvider` are
  interchangeable behind the Protocol. `BronzeCollector` is not edited apart from one log message.
- **Dependency Injection:** the handler's composition root swaps the injected provider. No
  provider-selection flag and no dual-read mode: Terraform deploys code and configuration of an
  environment together, so each environment switches atomically with its apply.
- **Unchanged payload:** each parameter holds the same JSON document as the secret today
  (`{"api_key": "...", "api_secret": "..."}`, far below the 4 KB standard-tier limit). One parameter
  per credential set keeps the Protocol contract and the rotation procedure one-to-one.
- **Parameters are not Terraform resources.** `aws_ssm_parameter` stores the value in the state
  (the pinned provider `< 6.0` has no write-only value), which would undo FEATURE-018 task 18.2.
  The owner creates and rotates the parameters with the CLI; Terraform only derives their names
  and ARNs from a naming convention for IAM and for the Lambda environment variables.
- **Naming:** `/vlc-market-pulse/{env}/idealista/lvw-api-credentials` and
  `.../pmv-api-credentials`, encrypted with the AWS-managed key `alias/aws/ssm` (no key charge).
- **Deliberately not done:** no caching layer, no Parameters-and-Secrets Lambda extension, no
  customer-managed KMS key ($1/month would cancel most of the saving).

## Approach

Order matters: every step is additive until the cut-over, and nothing is deleted before prod has
completed one scheduled run on the new path.

### Phase 1 — Adapter (code only, no behaviour change)
- [ ] **19.1** RED: tests for `SsmParameterProvider` with moto (`moto[s3,secretsmanager,ssm]`):
      returns the decoded mapping; missing parameter → `SecretError`; invalid JSON → `SecretError`;
      requests decryption. GREEN: implement the adapter and export it from `common/__init__.py`.
      Branch `feature/ssm-parameter-store-credentials/19.1-ssm-provider`.

### Phase 2 — IAM groundwork (local apply, additive)
- [ ] **19.2** `infrastructure/shared/github-oidc`: the workload boundary additionally allows
      `ssm:GetParameter` on `parameter/vlc-market-pulse/*/idealista/*`; plan and apply roles get an
      explicit deny for `ssm:GetParameter*`, `ssm:GetParametersByPath` and `ssm:PutParameter` on
      that path (parity with `DenySecretValues`). Extend the `simulate_apply_role.py` matrix first
      (RED), then the policy. **Applied locally by the owner**, as every change to this stack.
      Branch `.../19.2-iam-groundwork`.

### Phase 3 — Create the parameters (owner, local, no agent, no CI)
- [ ] **19.3** Add `infrastructure/scripts/migrate_idealista_credentials.sh`: for one environment
      it pipes each secret value straight into `aws ssm put-parameter --type SecureString` without
      printing it, then verifies existence with `describe-parameters` (metadata only). Document it
      in the README. The owner runs it for dev and prod. Branch `.../19.3-migration-script`.

### Phase 4 — Cut-over (one PR, so the dev smoke run is spent once)
- [ ] **19.4** `modules/lambda_bronze`: replace the four `secret_*` variables with the two parameter
      names; role policy becomes `ssm:GetParameter` on the two parameter ARNs; environment variables
      become `CREDENTIALS_PARAM_LVW` / `CREDENTIALS_PARAM_PMV`. Handler reads the new variables and
      injects `SsmParameterProvider()`; handler tests updated first (RED). dev and prod `main.tf`
      pass the names from a local; the `secrets` module stays in place for now.
      Branch `.../19.4-cut-over`.
- [ ] **19.5** Roll out and verify. dev: the merge triggers apply plus the full-pipeline smoke run
      (bronze paths changed) — the single allowed dev execution; bronze must write both operations.
      prod: dispatch and approve the apply early in the week, check the role with
      `aws iam simulate-principal-policy` and the parameters with `describe-parameters`, then
      confirm the next scheduled Sunday run and the Pipeline Health tab. The prod state machine is
      never started by hand.

### Phase 5 — Cleanup (only after the first green prod Sunday run)
- [ ] **19.6** Remove `modules/secrets`, its outputs and the obsolete `removed` blocks from dev and
      prod; the apply schedules the four secrets for deletion (7-day recovery window). Remove
      `SecretsManagerProvider`, its tests and the `secretsmanager` moto extra.
      Branch `.../19.6-remove-secrets-manager`.
- [ ] **19.7** `shared/github-oidc` (local apply, after 19.6 is applied in both environments): drop
      the Secrets Manager statements from boundary, plan role and apply role; update the matrix.
      Docs: README rotation section, `CLAUDE.md` (diagram, tech stack, features),
      `documentation/DATA_COLLECTION_LAYER.md`, `documentation/CI_CD.md`, `src/etl/README.md`,
      `src/etl/tests/README.md`. Branch `.../19.7-iam-cleanup-and-docs`.

## Files

- **Create:** `infrastructure/scripts/migrate_idealista_credentials.sh` — one-off, value-blind copy.
- **Change:** `src/etl/common/secrets_provider.py`, `src/etl/common/__init__.py` — add the SSM
  adapter (19.1), remove the Secrets Manager adapter (19.6).
- **Change:** `src/etl/data_collection/idealista_listings_collector.py` — new variable names and
  injected provider; `bronze_collector.py` — log message only.
- **Change:** `infrastructure/modules/lambda_bronze/{main,variables}.tf` — policy, variables, env.
- **Change:** `infrastructure/environments/{dev,prod}/{main,outputs}.tf` — parameter names; later
  removal of the `secrets` module and its outputs.
- **Change:** `infrastructure/shared/github-oidc/main.tf`, `simulate_apply_role.py` — boundary and
  CI role statements, matrix.
- **Delete (19.6):** `infrastructure/modules/secrets/`.
- **Change:** `src/etl/requirements-dev.txt` — moto extras.
- **Tests:** `src/etl/common/tests/test_secrets_provider.py`,
  `src/etl/data_collection/tests/` (handler wiring).
- **Docs:** see 19.7.

## Test strategy

- **Unit:** adapter against moto SSM (happy path, missing parameter, malformed JSON, decryption
  flag); handler wiring (missing variables raise the configuration error, the SSM adapter is
  injected). Existing `BronzeCollector` tests stay untouched — proof that the Protocol held.
- **Policy:** `simulate_apply_role.py` matrix covers: plan and apply roles cannot read or write the
  parameter values; apply role can still manage what it manages today.
- **Integration:** one dev pipeline run through the smoke job after the cut-over merge.
- **Manual:** `terraform plan` in dev and prod shows only the expected Lambda, policy and (later)
  secret-removal changes; `simulate-principal-policy` for the prod bronze role; first prod Sunday
  run green in the Pipeline Health tab.

## Estimated monthly cloud cost

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| Secrets Manager (removed) | $0.40 per secret per month | 4 secrets (2 × dev, 2 × prod) | −$1.60 |
| SSM Parameter Store, standard tier | free storage, free standard throughput | 4 parameters, ~16 reads | $0.00 |
| KMS, AWS-managed key `aws/ssm` | no key fee; $0.03 per 10,000 requests | ~16 decrypts | ~$0.00 |
| **Total change** | | | **≈ −$1.60/month (≈ −$19/year)** |

- **Cost drivers & cheaper alternatives:** nothing cheaper exists for encrypted storage. A
  customer-managed key or the advanced parameter tier would each add cost and are not needed.
- **External / non-AWS costs:** none. The cut-over spends one dev pipeline run of the shared
  Idealista quota (100 requests/month).
- **Budget check:** yes — lowers the bill; the project stays under $5/month.

## Success criteria

- [ ] Bronze Lambda in dev and prod reads both credential sets from Parameter Store; one dev run
      and one scheduled prod run are green on the new path.
- [ ] No Secrets Manager secret of this project remains (after the recovery window), and no
      `secretsmanager` reference remains in code, Terraform or docs.
- [ ] No credential value in Terraform configuration, plan output or state; plan and apply roles
      are explicitly denied the parameter values (matrix green).
- [ ] Rotation is documented as a single `aws ssm put-parameter --overwrite` command.
- [ ] Tests pass and coverage holds; conventions followed (type hints, docstrings).

## Open questions & risks

- **Question:** Is ~$19/year worth ~7 h of work plus one dev pipeline run? The change is safe but
  the payback is small; the stronger argument is one credential mechanism across both projects.
- **Question:** Use `/vlc-market-pulse/{env}/idealista/...` (assumed, matches the sibling project)
  or keep the old `{env}/idealista/...` shape?
- **Risk:** `kms:Decrypt` under the permissions boundary. The AWS-managed key normally lets account
  principals decrypt through SSM without an IAM statement, but the boundary may still require an
  explicit allow. *Mitigation:* the dev run in 19.5 proves it before prod; if it fails, add
  `kms:Decrypt` with `kms:ViaService = ssm.eu-central-1.amazonaws.com` to role and boundary.
- **Risk:** prod fails on its first Sunday run and a weekly snapshot is lost. *Mitigation:* dev runs
  the identical path first; IAM is simulated for the prod role; the secrets stay untouched until
  prod was green, so rollback is a revert plus apply.
- **Risk:** parameters exist outside Terraform and could be missing in a rebuilt environment.
  *Mitigation:* README setup step; the collector fails loudly with `SecretError` and the SNS alert.
- **Risk:** the migration prints or logs a value. *Mitigation:* the script pipes values and never
  echoes them; only the owner runs it locally — not CI and not an AI agent.
- **Assumption:** secrets scheduled for deletion are no longer billed during the recovery window.
- **Out of scope:** old state-bucket versions still contain earlier plaintext credentials (known
  from FEATURE-018). Rotating the Idealista credentials after the migration would close that.

## Progress log

- **2026-11-14** — Plan created.
