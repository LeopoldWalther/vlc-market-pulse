# CI/CD — GitHub Actions and AWS

How GitHub Actions authenticates to AWS and what each workflow may change.
Introduced by FEATURE-018 ([plan](../dev/plans/FEATURE-018-ci-terraform-deploy.md)).

## Deployment flow

```
pull request ──▶ terraform-validate.yml ──▶ fmt + validate + read-only plan (dev, prod)
                                            one PR comment, addresses only

merge to main ─▶ terraform-deploy.yml ────▶ apply dev ──▶ smoke test dev

manual dispatch ▶ terraform-deploy.yml ───▶ apply prod   (waits for the required reviewer;
  (main only)                                             only `main` may deploy)

manual dispatch ▶ deploy-frontend.yml ────▶ upload frontend + invalidate CloudFront
```

- **Pull request:** nothing is changed in AWS. The plan runs with the read-only role and
  `-lock=false`, so it can never block or alter state.
- **Merge to `main`:** dev is applied automatically when the merge touches
  `infrastructure/environments/**`, `infrastructure/modules/**` or `src/etl/**` (Lambda zips are
  built from source). A smoke test follows.
- **Prod:** start `Terraform Deploy` from the Actions tab (or
  `gh workflow run terraform-deploy.yml --ref main -f environment=prod`) and approve the waiting
  job. There is no automatic path to prod.
- **Frontend:** `Deploy Frontend` is always a manual dispatch, per environment.

### Smoke test and the Idealista quota

The Idealista quota is 100 requests per month, shared between dev and prod. The smoke test
therefore runs in dev only and never calls Idealista unless the bronze path changed:

| Change in the merge | Smoke path | Idealista requests |
|---|---|---|
| `src/etl/data_collection/`, `modules/lambda_bronze/` or `modules/pipeline_orchestrator/` | one `dev-medallion-pipeline` execution (dev runs in test mode: one page per operation) | 2 |
| anything else, and every manual dispatch | `dev-silver-cleaner` and `dev-gold-aggregator` invoked directly on existing data | 0 |

`dev-pipeline-health` is invoked in both cases and must return `statusCode` 200. The prod state
machine is never started by CI: the apply role holds `states:StartExecution` and
`lambda:InvokeFunction` for the dev smoke targets only (see the simulation matrix).

### Steps that stay local

CI must not be able to change its own permissions or read credentials, so the owner runs these by
hand:

| Step | When | Command |
|---|---|---|
| Apply `infrastructure/shared/github-oidc` | after every change to the CI roles or the workload boundary | `terraform plan -out=apply.plan && terraform apply apply.plan`, then `python simulate_apply_role.py --role github-actions-apply` |
| Rotate Idealista credentials | on expiry or leak | `aws secretsmanager put-secret-value` (README, "Rotating Idealista credentials") |
| Apply `infrastructure/bootstrap` | once per account | `terraform apply` |

A change to the CI roles is not live until it is applied locally. If a workflow fails with
`AccessDenied` right after such a change was merged, the local apply is the first thing to check
(this happened to the first smoke test of task 18.11).

## IAM roles for GitHub Actions

All roles are assumed through the GitHub OIDC provider (no long-lived keys) and
are defined in `infrastructure/shared/github-oidc/`. That stack is applied
**locally by the owner only** — CI can neither read its state nor change the
roles (see the guard rails below).

| Role | Repository variable | Trusted subjects | Purpose |
|---|---|---|---|
| `github-actions-plan` | `AWS_PLAN_ROLE_ARN` | `pull_request`, `ref:refs/heads/main` | read-only `terraform plan -lock=false` |
| `github-actions-apply` | `AWS_APPLY_ROLE_ARN` | `environment:dev`, `environment:prod` | `terraform apply` of `infrastructure/environments/*` |
| `github-actions-deploy` | `AWS_DEPLOY_ROLE_ARN` | `environment:dev`, `environment:prod`, `ref:refs/heads/main` | frontend upload + CloudFront invalidation |

Each subject is trusted in both GitHub formats — legacy
`repo:<owner>/<repo>:<context>` and immutable
`repo:<owner>@<owner_id>/<repo>@<repo_id>:<context>` (this repository uses the
immutable one; check with `gh api repos/<owner>/<repo>/actions/oidc/customization/sub`).

## Workflows

| Workflow | Trigger | Role | What it does |
|---|---|---|---|
| `terraform-validate.yml` | PR / push to `main` touching `infrastructure/**`, `src/etl/**` | `github-actions-plan` (PR only) | fmt + validate; on PRs a read-only plan for dev and prod and one PR comment with the address-only summary |
| `_terraform.yml` | `workflow_call` | plan or apply | reusable plan/apply for one environment (see below) |
| `terraform-deploy.yml` | push to `main` (stacks, `src/etl/**`) / manual | `github-actions-apply` | apply dev on merge and smoke-test it; apply dev or prod on dispatch (prod waits for approval) |
| `deploy-frontend.yml` | manual | `github-actions-deploy` | upload the frontend, invalidate CloudFront |

**Why the logs show no plan details.** The repository and its Actions logs are public, and a
Terraform plan prints attribute values such as the alert email address, SNS ARNs and the state
machine definition. `_terraform.yml` therefore never publishes attribute values: the plan's text output is
discarded, only `<actions> <address>` lines go to the step summary / outputs,
an apply log is reduced to its `Error` blocks, and the plan file is deleted (no
artifacts). It builds the gitignored `requests.zip` layer from Linux wheels when
it is missing.

## GitHub environments

The apply role trusts only jobs bound to the `dev` or `prod` environment, so the
environment rules are the last gate before a CI apply:

| Environment | Required reviewers | Deployment branches |
|---|---|---|
| `dev` | none | `main` only |
| `prod` | `LeopoldWalther` (self-review allowed — solo maintainer) | `main` only |

A job targeting `prod` waits for approval in the Actions UI; a job from any
other branch is rejected before it gets an OIDC token. No `TF_VAR_*` variables
are needed — the environments hold no Terraform input.

Reproduce the configuration (owner, `gh` authenticated as repository admin):

```bash
R=repos/LeopoldWalther/vlc-market-pulse
OWNER_ID=$(gh api user --jq .id)

gh api -X PUT $R/environments/prod --input - <<JSON
{"wait_timer":0,"prevent_self_review":false,
 "reviewers":[{"type":"User","id":$OWNER_ID}],
 "deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}
JSON

gh api -X PUT $R/environments/dev --input - <<JSON
{"wait_timer":0,"reviewers":[],
 "deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}
JSON

for env in dev prod; do
  gh api -X POST $R/environments/$env/deployment-branch-policies -f name=main -f type=branch
done
```

Check:

```bash
gh api repos/LeopoldWalther/vlc-market-pulse/environments/prod \
  --jq '[.protection_rules[].type], .deployment_branch_policy'
gh api repos/LeopoldWalther/vlc-market-pulse/environments/prod/deployment-branch-policies \
  --jq '[.branch_policies[].name]'
```

## Apply role design

The AWS account also hosts other projects whose resources share the `dev-` /
`prod-` prefixes (`*-vlc-market-oracle-*`, `vlc-housing-silver-*`). The apply
role is therefore scoped to **this project's explicit resource names** (locals
in `infrastructure/shared/github-oidc/main.tf`), never to `dev-*` / `prod-*`.

Its permissions are split across five managed policies (a managed policy is
capped at 6,144 characters):

| Policy | Content |
|---|---|
| `github-actions-apply-read` | Get/List/Describe for every resource type + env state read/write (incl. `.tflock`) |
| `github-actions-apply-iam` | writes on the six workload roles per env, PassRole to Lambda/Step Functions/Scheduler only |
| `github-actions-apply-compute` | Lambda functions, layers, log groups, Step Functions log delivery |
| `github-actions-apply-platform` | state machines, schedules, rules, alarms, SNS, bucket configuration, secret metadata, CloudFront, ACM, Route 53 records |
| `github-actions-apply-guard-rails` | explicit denies (below) |

Exceptions to name scoping, each documented in the code: CloudFront writes on
`*` (no name-based ARNs; only this project uses CloudFront in the account), ACM
certificates in `us-east-1`, and the vended-log-delivery actions Step Functions
requires on `*`. Route 53 changes are limited to the `vlc-market-pulse*`
records in `leopoldwalther.com`.

### Guard rails (explicit deny)

- Every workload role CI creates, gives an inline policy or a new boundary must
  carry `vlc-workload-boundary`; the boundary can never be removed.
- No IAM writes on `role/github-actions-*`, the OIDC provider or the boundary
  policy (reads stay allowed — the environments look the boundary up by name).
- No customer-managed policies and no policy attachments.
- No `secretsmanager:GetSecretValue` / `PutSecretValue` — credential values are
  rotated by the owner (see README, "Rotating Idealista credentials").
- No access to old state versions (`s3:GetObjectVersion`): versions from before
  FEATURE-018 task 18.2 contain plaintext credentials. No access to the
  `vlc-state/shared/*` state and no state-bucket configuration changes.
- The listings buckets (the collected data) can never be deleted.

### Simulation matrix

Re-run after every change to the apply role:

```bash
cd infrastructure/shared/github-oidc
terraform plan -out=apply.plan && terraform show -json apply.plan > apply.json
python simulate_apply_role.py --plan apply.json            # before apply
python simulate_apply_role.py --role github-actions-apply  # after apply
```

Result (2026-11-14, `--role github-actions-apply` against the deployed role, 34/34 as expected):

`explicit-deny` requires a matching Deny statement; a merely absent Allow fails the case.
¹ Added with FEATURE-019 task 19.2; the plan role returns `explicitDeny` for both actions as well.

| Action | Resource | Expected | Result |
|---|---|---|---|
| `lambda:UpdateFunctionConfiguration` | `prod-idealista-collector` | allow | allowed |
| `lambda:UpdateFunctionCode` | `dev-silver-cleaner` | allow | allowed |
| `states:UpdateStateMachine` | `dev-medallion-pipeline` | allow | allowed |
| `iam:PutRolePolicy` | `role/dev-silver-cleaner-lambda-role` (+ctx) | allow | allowed |
| `iam:CreateRole` | `role/dev-silver-cleaner-lambda-role` (+ctx) | allow | allowed |
| `iam:PassRole` | `role/prod-medallion-pipeline-sfn-role` (+ctx) | allow | allowed |
| `iam:GetPolicy` | `policy/vlc-workload-boundary` | allow | allowed |
| `s3:PutObject` | `vlc-real-estate-analytics-tf-state/vlc-state/prod/terraform.tfstate.tflock` | allow | allowed |
| `s3:ListBucket` | `prod-vlc-real-estate-analytics-listings` | allow | allowed |
| `route53:ChangeResourceRecordSets` | `hostedzone/Z001078818QR783M1TOKB` (+ctx) | allow | allowed |
| `lambda:InvokeFunction` | `dev-silver-cleaner` | allow | allowed |
| `states:StartExecution` | `dev-medallion-pipeline` | allow | allowed |
| `iam:CreateRole` | `role/dev-silver-cleaner-lambda-role` | deny | explicitDeny |
| `iam:CreateRole` | `role/dev-x` (+ctx) | deny | implicitDeny |
| `iam:PutRolePolicy` | `role/dev-vlc-market-oracle-collector-role` (+ctx) | deny | implicitDeny |
| `iam:UpdateAssumeRolePolicy` | `role/github-actions-deploy` | deny | explicitDeny |
| `iam:CreatePolicyVersion` | `policy/vlc-workload-boundary` | deny | explicitDeny |
| `iam:DeleteRolePermissionsBoundary` | `role/dev-silver-cleaner-lambda-role` | deny | explicitDeny |
| `iam:AttachRolePolicy` | `role/dev-silver-cleaner-lambda-role` (+ctx) | deny | explicitDeny |
| `iam:PassRole` | `role/dev-silver-cleaner-lambda-role` (+ctx) | deny | implicitDeny |
| `secretsmanager:GetSecretValue` | `prod/idealista/lvw-api-credentials-x` | deny | explicitDeny |
| `secretsmanager:PutSecretValue` | `prod/idealista/lvw-api-credentials-x` | deny | explicitDeny |
| `ssm:GetParameter` | `parameter/vlc-market-pulse/prod/idealista/lvw-api-credentials` | explicit-deny | explicitDeny ¹ |
| `ssm:PutParameter` | `parameter/vlc-market-pulse/prod/idealista/lvw-api-credentials` | explicit-deny | explicitDeny ¹ |
| `s3:PutObject` | `vlc-real-estate-analytics-tf-state/vlc-state/shared/github-oidc/terraform.tfstate` | deny | explicitDeny |
| `s3:GetObjectVersion` | `vlc-real-estate-analytics-tf-state/vlc-state/prod/terraform.tfstate` | deny | explicitDeny |
| `s3:DeleteBucket` | `prod-vlc-real-estate-analytics-listings` | deny | explicitDeny |
| `s3:GetObject` | `prod-vlc-real-estate-analytics-listings/bronze/x.json` | deny | implicitDeny |
| `lambda:InvokeFunction` | `prod-idealista-collector` | deny | implicitDeny |
| `lambda:InvokeFunction` | `prod-silver-cleaner` | deny | implicitDeny |
| `lambda:InvokeFunction` | `prod-pipeline-health` | deny | implicitDeny |
| `states:StartExecution` | `prod-medallion-pipeline` | deny | implicitDeny |
| `lambda:UpdateFunctionCode` | `prod-vlc-market-oracle-budget-gate` | deny | implicitDeny |
| `route53:ChangeResourceRecordSets` | `hostedzone/Z001078818QR783M1TOKB` (+ctx) | deny | implicitDeny |

### Extending the apply role

A new resource in `infrastructure/environments/*` needs its name (and, for a
new service, its actions) added to the locals / statements in
`infrastructure/shared/github-oidc/main.tf`, a matrix case in
`simulate_apply_role.py`, and a **local** apply by the owner. A CI apply that
fails with `AccessDenied` names the missing action in its log.

## End-to-end verification (FEATURE-018 task 18.12)

| Check | Expected | Result |
|---|---|---|
| Merge to `main` applies dev and the smoke test passes | apply and smoke green | ✅ [run 43281993227](https://github.com/LeopoldWalther/vlc-market-pulse/actions/runs/43281993227) (smoke green on re-run after the local `github-oidc` apply, 2026-11-13) |
| Prod dispatch from a non-`main` branch | rejected before any step runs | ✅ [run 43517120867](https://github.com/LeopoldWalther/vlc-market-pulse/actions/runs/43517120867): "Branch … is not allowed to deploy to prod due to environment protection rules" |
| Prod dispatch from `main` | waits for approval; after approval no or only expected changes | ✅ [run 43541532630](https://github.com/LeopoldWalther/vlc-market-pulse/actions/runs/43541532630) (2026-11-14): waited for the `prod` reviewer, apply green after approval; it shipped the gold aggregator code of FEATURE-016 |
| `deploy-frontend.yml` for prod | succeeds | ✅ [run 43541303560](https://github.com/LeopoldWalther/vlc-market-pulse/actions/runs/43541303560) (2026-11-14), also gated by the `prod` reviewer |
