# CI/CD — GitHub Actions and AWS

How GitHub Actions authenticates to AWS and what each workflow may change.
Introduced by FEATURE-018 ([plan](../dev/plans/FEATURE-018-ci-terraform-deploy.md)).

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

Result (2026-11-10, planned and deployed policies identical):

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
| `route53:ChangeResourceRecordSets` | `hostedzone/Z001078818QR783M1TOKB` (+ctx) | allow | allowed |
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
| `s3:PutObject` | `vlc-real-estate-analytics-tf-state/vlc-state/shared/github-oidc/terraform.tfstate` | deny | explicitDeny |
| `s3:GetObjectVersion` | `vlc-real-estate-analytics-tf-state/vlc-state/prod/terraform.tfstate` | deny | explicitDeny |
| `s3:DeleteBucket` | `prod-vlc-real-estate-analytics-listings` | deny | explicitDeny |
| `s3:GetObject` | `prod-vlc-real-estate-analytics-listings/bronze/x.json` | deny | implicitDeny |
| `lambda:UpdateFunctionCode` | `prod-vlc-market-oracle-budget-gate` | deny | implicitDeny |
| `route53:ChangeResourceRecordSets` | `hostedzone/Z001078818QR783M1TOKB` (+ctx) | deny | implicitDeny |

### Extending the apply role

A new resource in `infrastructure/environments/*` needs its name (and, for a
new service, its actions) added to the locals / statements in
`infrastructure/shared/github-oidc/main.tf`, a matrix case in
`simulate_apply_role.py`, and a **local** apply by the owner. A CI apply that
fails with `AccessDenied` names the missing action in its log.
