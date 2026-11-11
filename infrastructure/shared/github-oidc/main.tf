# ---------------------------------------------------------------------------
# GitHub Actions OIDC provider — account-global, created once.
#
# GitHub's OIDC issuer publishes a JSON Web Key Set at:
# https://token.actions.githubusercontent.com/.well-known/openid-configuration
#
# The thumbprint below is GitHub's root CA thumbprint for the OIDC endpoint.
# AWS now auto-fetches and rotates thumbprints for recognised OIDC providers,
# but at least one value must be supplied in the resource arguments.
# ---------------------------------------------------------------------------
resource "aws_iam_openid_connect_provider" "github_actions" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]

  tags = {
    Name      = "github-actions-oidc"
    ManagedBy = "terraform"
  }
}

# ---------------------------------------------------------------------------
# Data source — current account ID, used to build ARNs without hard-coding.
# ---------------------------------------------------------------------------
data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Trust policy — allows GitHub Actions to assume the deploy role when the
# workflow token matches the expected repository and environment/ref.
#
# StringEquals on :aud ensures only sts.amazonaws.com audience tokens are
# accepted (prevents confused-deputy attacks from other OIDC clients).
# StringLike on :sub scopes the role to specific environments and the main
# branch (used by workflows that run without an environment).
#
# GitHub issues the :sub claim in one of two formats:
#   legacy    repo:<owner>/<repo>:<context>
#   immutable repo:<owner>@<owner_id>/<repo>@<repo_id>:<context>
# This repository uses immutable subjects. Both prefixes are trusted so the
# role keeps working if the repository setting is switched back. Check the
# current prefix with:
#   gh api repos/<owner>/<repo>/actions/oidc/customization/sub
# ---------------------------------------------------------------------------
locals {
  github_subject_prefixes = [
    "repo:${var.github_org}/${var.github_repo}",
    "repo:${var.github_org}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}",
  ]

  github_deploy_subject_suffixes = [
    ":environment:dev",
    ":environment:prod",
    ":ref:refs/heads/main",
  ]

  github_deploy_subjects = [
    for pair in setproduct(local.github_subject_prefixes, local.github_deploy_subject_suffixes) :
    join("", pair)
  ]
}

data "aws_iam_policy_document" "github_actions_trust" {
  statement {
    sid     = "AllowGitHubActionsOIDC"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.github_deploy_subjects
    }
  }
}

# ---------------------------------------------------------------------------
# Least-privilege permission policy — grants only the actions the deploy
# workflows need and nothing more.
#
# Permissions granted:
#   • S3 frontend asset buckets (dev + prod): PutObject, DeleteObject, ListBucket
#     Bucket names follow the pattern: ${environment}-vlc-frontend-assets
#   • CloudFront: CreateInvalidation (must target "*" — no resource-level ARNs)
#   • Terraform remote state: GetObject on the dev/prod state keys only (for
#     `terraform output`), ListBucket on the state bucket. The shared/github-oidc
#     state is deliberately unreadable from CI.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "github_actions_deploy" {

  # Frontend asset bucket — dev: PutObject + DeleteObject on objects
  statement {
    sid    = "S3FrontendDevObjects"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["arn:aws:s3:::dev-vlc-frontend-assets/*"]
  }

  # Frontend asset bucket — dev: ListBucket on the bucket itself
  statement {
    sid       = "S3FrontendDevBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::dev-vlc-frontend-assets"]
  }

  # Frontend asset bucket — prod: PutObject + DeleteObject on objects
  statement {
    sid    = "S3FrontendProdObjects"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["arn:aws:s3:::prod-vlc-frontend-assets/*"]
  }

  # Frontend asset bucket — prod: ListBucket on the bucket itself
  statement {
    sid       = "S3FrontendProdBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::prod-vlc-frontend-assets"]
  }

  # CloudFront — cache invalidation after every frontend deploy.
  # CRITICAL: CloudFront does not support resource-level ARN scoping for
  # CreateInvalidation; AWS requires the resource to be "*".
  statement {
    sid       = "CloudFrontInvalidation"
    effect    = "Allow"
    actions   = ["cloudfront:CreateInvalidation"]
    resources = ["*"]
  }

  # Terraform remote state — needed for `terraform output` in CI steps.
  # Scoped to the environment state keys: the shared/github-oidc state
  # describes the CI roles themselves and is not needed by any workflow.
  statement {
    sid       = "TerraformStateGetObject"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = local.environment_state_object_arns
  }

  statement {
    sid       = "TerraformStateBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::vlc-real-estate-analytics-tf-state"]
  }

}

# ---------------------------------------------------------------------------
# Deploy IAM role — assumed by GitHub Actions via the OIDC provider above.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "github_actions_deploy" {
  name               = "github-actions-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_actions_trust.json
  description        = "Assumed by GitHub Actions via OIDC; scoped to deploy workflows only."

  tags = {
    Name      = "github-actions-deploy"
    ManagedBy = "terraform"
  }
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  name   = "github-actions-deploy-policy"
  role   = aws_iam_role.github_actions_deploy.id
  policy = data.aws_iam_policy_document.github_actions_deploy.json
}

# ---------------------------------------------------------------------------
# Permissions boundary for the workload roles (Lambda, Step Functions,
# Scheduler) created by infrastructure/environments/*.
#
# Why: the CI apply role may create and change workload roles. A role it
# creates must carry this boundary (enforced in the apply role's policy), so
# even a malicious inline policy can never grant more than what is listed
# here — in particular no IAM, no secret writes and nothing outside this
# project's resources.
#
# The Allow list is the union of every action in the aws_iam_role_policy
# resources under infrastructure/modules/ (dev/tools/iam_action_inventory.sh
# prints it; `--check` simulates each action against the deployed policy).
# Resource scoping is deliberately a little broader than the inline policies,
# which still narrow it per role. It is limited to this project's resource
# names because the account also hosts other dev-*/prod-* workloads.
#
# Applied LOCALLY only — CI must never be able to change its own guard rail.
# ---------------------------------------------------------------------------
locals {
  account_id = data.aws_caller_identity.current.account_id

  workload_functions = [
    "idealista-collector",
    "silver-cleaner",
    "gold-aggregator",
    "pipeline-health",
  ]

  workload_function_arns = flatten([
    for env in ["dev", "prod"] : [
      for fn in local.workload_functions : [
        "arn:aws:lambda:*:${local.account_id}:function:${env}-${fn}",
        "arn:aws:lambda:*:${local.account_id}:function:${env}-${fn}:*",
      ]
    ]
  ])

  workload_log_group_arns = flatten([
    for env in ["dev", "prod"] : concat(
      [for fn in local.workload_functions : "arn:aws:logs:*:${local.account_id}:log-group:/aws/lambda/${env}-${fn}:*"],
      ["arn:aws:logs:*:${local.account_id}:log-group:/aws/vendedlogs/states/${env}-medallion-pipeline:*"],
    )
  ])
}

data "aws_iam_policy_document" "workload_boundary" {
  # Lambda and Step Functions logging, plus Logs Insights queries run by the
  # pipeline-health Lambda against the other functions' log groups.
  statement {
    sid    = "LogsOnProjectLogGroups"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:StartQuery",
    ]
    resources = local.workload_log_group_arns
  }

  # AWS only accepts Resource "*" for these: log delivery (Step Functions
  # logging), resource policies, query results and log group discovery.
  statement {
    sid    = "LogsAccountLevel"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:DescribeLogGroups",
      "logs:DescribeResourcePolicies",
      "logs:GetLogDelivery",
      "logs:GetQueryResults",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:StopQuery",
      "logs:UpdateLogDelivery",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ListingsBucketObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:PutObjectAcl",
    ]
    resources = [
      "arn:aws:s3:::dev-vlc-real-estate-analytics-listings/*",
      "arn:aws:s3:::prod-vlc-real-estate-analytics-listings/*",
    ]
  }

  statement {
    sid     = "ListingsBucketList"
    effect  = "Allow"
    actions = ["s3:ListBucket"]
    resources = [
      "arn:aws:s3:::dev-vlc-real-estate-analytics-listings",
      "arn:aws:s3:::prod-vlc-real-estate-analytics-listings",
    ]
  }

  # Read only. Secret values are written by the owner, never by a workload.
  statement {
    sid       = "IdealistaSecretsRead"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:*:${local.account_id}:secret:*/idealista/*"]
  }

  statement {
    sid       = "PipelineNotifications"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = ["arn:aws:sns:*:${local.account_id}:*-idealista-notifications"]
  }

  # Metrics and Cost Explorer have no resource-level permissions.
  statement {
    sid    = "MetricsAndCost"
    effect = "Allow"
    actions = [
      "cloudwatch:GetMetricData",
      "cloudwatch:PutMetricData",
      "ce:GetCostAndUsage",
    ]
    resources = ["*"]
  }

  # Step Functions invokes the stage Lambdas.
  statement {
    sid       = "InvokePipelineLambdas"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = local.workload_function_arns
  }

  # EventBridge Scheduler starts the weekly pipeline run.
  statement {
    sid       = "StartPipeline"
    effect    = "Allow"
    actions   = ["states:StartExecution"]
    resources = ["arn:aws:states:*:${local.account_id}:stateMachine:*-medallion-pipeline"]
  }
}

resource "aws_iam_policy" "vlc_workload_boundary" {
  name        = "vlc-workload-boundary"
  description = "Permissions boundary for VLC Market Pulse workload roles (dev and prod)."
  policy      = data.aws_iam_policy_document.workload_boundary.json

  tags = {
    Name      = "vlc-workload-boundary"
    ManagedBy = "terraform"
  }
}

# ---------------------------------------------------------------------------
# Terraform state locations shared by the CI roles.
#
# CRITICAL: older object versions in the state bucket still contain the
# Idealista credentials that Terraform managed before FEATURE-018 task 18.2.
# No CI role may ever get s3:GetObjectVersion on this bucket; the plan role
# denies it explicitly as defence in depth.
# ---------------------------------------------------------------------------
locals {
  state_bucket_arn = "arn:aws:s3:::vlc-real-estate-analytics-tf-state"

  environment_state_object_arns = [
    "${local.state_bucket_arn}/vlc-state/dev/*",
    "${local.state_bucket_arn}/vlc-state/prod/*",
  ]
}

# ---------------------------------------------------------------------------
# Read-only plan role — assumed by pull-request workflows to run
# `terraform plan -lock=false` against dev and prod.
#
# Why a separate role: pull_request runs execute code from an unmerged
# branch, so the role they get must not be able to change anything. It may
# read resource configuration and the environment state, nothing else — no
# state writes (hence -lock=false), no secret values, no old state versions.
# ---------------------------------------------------------------------------
# Configuration reads `terraform plan` needs to refresh every resource type
# under infrastructure/environments and infrastructure/modules. Shared by the
# plan and the apply role so both see the same infrastructure.
locals {
  terraform_read_actions = [
    "acm:DescribeCertificate",
    "acm:GetCertificate",
    "acm:ListCertificates",
    "acm:ListTagsForCertificate",
    "cloudfront:Get*",
    "cloudfront:List*",
    "cloudwatch:DescribeAlarms",
    "cloudwatch:ListTagsForResource",
    "events:Describe*",
    "events:List*",
    "iam:GetOpenIDConnectProvider",
    "iam:GetPolicy",
    "iam:GetPolicyVersion",
    "iam:GetRole",
    "iam:GetRolePolicy",
    "iam:ListAttachedRolePolicies",
    "iam:ListInstanceProfilesForRole",
    "iam:ListPolicies",
    "iam:ListPolicyVersions",
    "iam:ListRolePolicies",
    "iam:ListRoleTags",
    "lambda:Get*",
    "lambda:List*",
    "logs:DescribeLogGroups",
    "logs:ListTagsForResource",
    "logs:ListTagsLogGroup",
    "route53:Get*",
    "route53:List*",
    "s3:GetAccelerateConfiguration",
    "s3:GetBucket*",
    "s3:GetEncryptionConfiguration",
    "s3:GetLifecycleConfiguration",
    "s3:GetReplicationConfiguration",
    "scheduler:Get*",
    "scheduler:List*",
    "secretsmanager:DescribeSecret",
    "secretsmanager:GetResourcePolicy",
    "secretsmanager:ListSecrets",
    "sns:GetSubscriptionAttributes",
    "sns:GetTopicAttributes",
    "sns:ListSubscriptionsByTopic",
    "sns:ListTagsForResource",
    "states:Describe*",
    "states:List*",
  ]
}

locals {
  github_plan_subjects = [
    for pair in setproduct(local.github_subject_prefixes, [":pull_request", ":ref:refs/heads/main"]) :
    join("", pair)
  ]
}

data "aws_iam_policy_document" "github_actions_plan_trust" {
  statement {
    sid     = "AllowGitHubActionsOIDCPlan"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.github_plan_subjects
    }
  }
}

data "aws_iam_policy_document" "github_actions_plan" {

  # Configuration reads for every resource type under infrastructure/environments
  # and infrastructure/modules — what `terraform plan` needs to refresh state.
  # Wildcard Get/List/Describe is acceptable here because none of these
  # services expose secret material through those calls; the secret and S3
  # object reads that would are excluded below.
  statement {
    sid       = "ReadResourceConfiguration"
    effect    = "Allow"
    actions   = local.terraform_read_actions
    resources = ["*"]
  }

  # The AWS provider checks a bucket's existence with HeadBucket, which needs
  # s3:ListBucket. Without it the buckets look deleted and the plan proposes
  # to recreate them (seen in the first CI plan, PR #54). Listing keys only —
  # no object reads.
  statement {
    sid       = "ProjectBucketExistence"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = concat(local.listings_bucket_arns, local.frontend_bucket_arns)
  }

  statement {
    sid       = "TerraformStateRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = local.environment_state_object_arns
  }

  statement {
    sid       = "TerraformStateBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket_arn]
  }

  # Defence in depth: these must stay impossible even if an Allow above is
  # widened by mistake later.
  statement {
    sid    = "DenyStateHistoryAndWrites"
    effect = "Deny"
    actions = [
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
    ]
    resources = ["${local.state_bucket_arn}/*"]
  }

  statement {
    sid       = "DenySecretValues"
    effect    = "Deny"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["*"]
  }
}

resource "aws_iam_role" "github_actions_plan" {
  name                 = "github-actions-plan"
  assume_role_policy   = data.aws_iam_policy_document.github_actions_plan_trust.json
  description          = "Assumed by GitHub Actions pull-request workflows via OIDC; read-only terraform plan."
  max_session_duration = 3600

  tags = {
    Name      = "github-actions-plan"
    ManagedBy = "terraform"
  }
}

resource "aws_iam_role_policy" "github_actions_plan" {
  name   = "github-actions-plan-policy"
  role   = aws_iam_role.github_actions_plan.id
  policy = data.aws_iam_policy_document.github_actions_plan.json
}

# ---------------------------------------------------------------------------
# Apply role — assumed only by jobs bound to the GitHub `dev` or `prod`
# environment (prod requires a reviewer, see documentation/CI_CD.md) to run
# `terraform apply` for infrastructure/environments/*.
#
# Scoping rule: writes are granted on this project's explicit resource names
# only. The AWS account also hosts other projects (`*-vlc-market-oracle-*`,
# `vlc-housing-silver-*`) whose names share the `dev-`/`prod-` prefixes, so
# `dev-*` / `prod-*` wildcards would hand CI control over them.
#
# Guard rails (explicit Deny, cannot be undone by any Allow):
#   • every role it creates or edits must carry vlc-workload-boundary, and the
#     boundary can never be removed or swapped
#   • no IAM writes on the CI roles, the OIDC provider or the boundary policy
#   • no customer-managed policies, no policy attachments (inline only)
#   • no secret values, no state history, no shared/github-oidc state
#   • the listings buckets (the collected data) can never be deleted
# ---------------------------------------------------------------------------
locals {
  environments       = ["dev", "prod"]
  aws_region         = "eu-central-1"
  route53_zone_id    = "Z001078818QR783M1TOKB" # leopoldwalther.com
  boundary_policy_id = "arn:aws:iam::${local.account_id}:policy/vlc-workload-boundary"

  workload_role_suffixes = [
    "idealista-collector-lambda-role",
    "silver-cleaner-lambda-role",
    "gold-aggregator-lambda-role",
    "pipeline-health-lambda-role",
    "medallion-pipeline-sfn-role",
    "medallion-pipeline-scheduler-role",
  ]

  workload_role_arns = [
    for pair in setproduct(local.environments, local.workload_role_suffixes) :
    "arn:aws:iam::${local.account_id}:role/${pair[0]}-${pair[1]}"
  ]

  apply_function_arns = [
    for pair in setproduct(local.environments, local.workload_functions) :
    "arn:aws:lambda:${local.aws_region}:${local.account_id}:function:${pair[0]}-${pair[1]}"
  ]

  apply_layer_arns = flatten([
    for env in local.environments : [
      "arn:aws:lambda:${local.aws_region}:${local.account_id}:layer:${env}-requests-layer",
      "arn:aws:lambda:${local.aws_region}:${local.account_id}:layer:${env}-requests-layer:*",
    ]
  ])

  apply_log_group_arns = flatten([
    for env in local.environments : concat(
      [for fn in local.workload_functions : "arn:aws:logs:${local.aws_region}:${local.account_id}:log-group:/aws/lambda/${env}-${fn}"],
      [for fn in local.workload_functions : "arn:aws:logs:${local.aws_region}:${local.account_id}:log-group:/aws/lambda/${env}-${fn}:*"],
      [
        "arn:aws:logs:${local.aws_region}:${local.account_id}:log-group:/aws/vendedlogs/states/${env}-medallion-pipeline",
        "arn:aws:logs:${local.aws_region}:${local.account_id}:log-group:/aws/vendedlogs/states/${env}-medallion-pipeline:*",
      ],
    )
  ])

  apply_event_rule_arns = [
    for pair in setproduct(local.environments, local.workload_functions) :
    "arn:aws:events:${local.aws_region}:${local.account_id}:rule/${pair[0]}-${pair[1]}-weekly"
  ]

  apply_schedule_arns = [
    for env in local.environments :
    "arn:aws:scheduler:${local.aws_region}:${local.account_id}:schedule/default/${env}-medallion-pipeline-weekly"
  ]

  apply_state_machine_arns = flatten([
    for env in local.environments : [
      "arn:aws:states:${local.aws_region}:${local.account_id}:stateMachine:${env}-medallion-pipeline",
      "arn:aws:states:${local.aws_region}:${local.account_id}:stateMachine:${env}-medallion-pipeline:*",
    ]
  ])

  apply_alarm_arns = flatten([
    for env in local.environments : [
      "arn:aws:cloudwatch:${local.aws_region}:${local.account_id}:alarm:${env}-silver-cleaner-errors",
      "arn:aws:cloudwatch:${local.aws_region}:${local.account_id}:alarm:${env}-gold-aggregator-errors",
    ]
  ])

  apply_topic_arns = flatten([
    for env in local.environments : [
      "arn:aws:sns:${local.aws_region}:${local.account_id}:${env}-idealista-notifications",
      "arn:aws:sns:${local.aws_region}:${local.account_id}:${env}-idealista-notifications:*",
    ]
  ])

  listings_bucket_arns = [for env in local.environments : "arn:aws:s3:::${env}-vlc-real-estate-analytics-listings"]
  frontend_bucket_arns = [for env in local.environments : "arn:aws:s3:::${env}-vlc-frontend-assets"]

  apply_secret_arns = flatten([
    for env in local.environments : [
      "arn:aws:secretsmanager:${local.aws_region}:${local.account_id}:secret:${env}/idealista/lvw-api-credentials-*",
      "arn:aws:secretsmanager:${local.aws_region}:${local.account_id}:secret:${env}/idealista/pmv-api-credentials-*",
    ]
  ])

  github_apply_subjects = [
    for pair in setproduct(local.github_subject_prefixes, [":environment:dev", ":environment:prod"]) :
    join("", pair)
  ]
}

data "aws_iam_policy_document" "github_actions_apply_trust" {
  statement {
    sid     = "AllowGitHubActionsOIDCApply"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.github_apply_subjects
    }
  }
}

# Allow statements — what `terraform apply` needs for the environment stacks.
# Split across managed policies (read, iam, compute, platform, guard rails) because a single
# managed policy is capped at 6,144 characters.
# Part 1 — configuration reads and the environment state.
data "aws_iam_policy_document" "github_actions_apply_read" {

  statement {
    sid       = "ReadResourceConfiguration"
    effect    = "Allow"
    actions   = local.terraform_read_actions
    resources = ["*"]
  }

  # The AWS provider checks a bucket's existence with HeadBucket, which needs
  # s3:ListBucket. Without it the buckets look deleted and the plan proposes
  # to recreate them (seen in the first CI plan, PR #54). Listing keys only —
  # no object reads.
  statement {
    sid       = "ProjectBucketExistence"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = concat(local.listings_bucket_arns, local.frontend_bucket_arns)
  }

  # State read/write incl. the S3-native lock file (<key>.tflock).
  statement {
    sid       = "TerraformStateReadWrite"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = local.environment_state_object_arns
  }

  statement {
    sid       = "TerraformStateBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket_arn]
  }
}

# Part 2 — the six workload roles of each environment.
data "aws_iam_policy_document" "github_actions_apply_iam" {

  # CRITICAL: a role can only be created, given an inline policy or a new
  # boundary when that boundary is vlc-workload-boundary. This caps whatever
  # CI writes into a workload role at the boundary's permissions.
  statement {
    sid       = "WorkloadRoleWritesWithBoundary"
    effect    = "Allow"
    actions   = ["iam:CreateRole", "iam:PutRolePolicy", "iam:PutRolePermissionsBoundary"]
    resources = local.workload_role_arns

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.boundary_policy_id]
    }
  }

  statement {
    sid    = "WorkloadRoleMaintenance"
    effect = "Allow"
    actions = [
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
    ]
    resources = local.workload_role_arns
  }

  statement {
    sid       = "PassWorkloadRoles"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = local.workload_role_arns

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com", "states.amazonaws.com", "scheduler.amazonaws.com"]
    }
  }
}

# Part 3 — Lambda functions, layers and their log groups.
data "aws_iam_policy_document" "github_actions_apply_compute" {

  statement {
    sid    = "LambdaFunctions"
    effect = "Allow"
    actions = [
      "lambda:AddPermission",
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:DeleteFunctionConcurrency",
      "lambda:PublishVersion",
      "lambda:PutFunctionConcurrency",
      "lambda:RemovePermission",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
    ]
    resources = local.apply_function_arns
  }

  statement {
    sid       = "LambdaLayers"
    effect    = "Allow"
    actions   = ["lambda:PublishLayerVersion", "lambda:DeleteLayerVersion"]
    resources = local.apply_layer_arns
  }

  statement {
    sid    = "LogGroups"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:DeleteRetentionPolicy",
      "logs:PutRetentionPolicy",
      "logs:TagLogGroup",
      "logs:TagResource",
      "logs:UntagLogGroup",
      "logs:UntagResource",
    ]
    resources = local.apply_log_group_arns
  }

  # Step Functions logging configuration: the caller of CreateStateMachine /
  # UpdateStateMachine needs these vended-log actions, which AWS only
  # authorises on "*".
  statement {
    sid    = "StateMachineLogDelivery"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:DescribeResourcePolicies",
      "logs:GetLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:UpdateLogDelivery",
    ]
    resources = ["*"]
  }
}

# Allow statements, part 4 — orchestration, notifications, storage and the
# frontend edge (split only to stay below the managed-policy size limit).
data "aws_iam_policy_document" "github_actions_apply_platform" {

  statement {
    sid    = "StateMachines"
    effect = "Allow"
    actions = [
      "states:CreateStateMachine",
      "states:DeleteStateMachine",
      "states:PublishStateMachineVersion",
      "states:TagResource",
      "states:UntagResource",
      "states:UpdateStateMachine",
      "states:ValidateStateMachineDefinition",
    ]
    resources = local.apply_state_machine_arns
  }

  statement {
    sid       = "Schedules"
    effect    = "Allow"
    actions   = ["scheduler:CreateSchedule", "scheduler:DeleteSchedule", "scheduler:UpdateSchedule"]
    resources = local.apply_schedule_arns
  }

  statement {
    sid    = "EventRules"
    effect = "Allow"
    actions = [
      "events:DeleteRule",
      "events:DisableRule",
      "events:EnableRule",
      "events:PutRule",
      "events:PutTargets",
      "events:RemoveTargets",
      "events:TagResource",
      "events:UntagResource",
    ]
    resources = local.apply_event_rule_arns
  }

  statement {
    sid       = "Alarms"
    effect    = "Allow"
    actions   = ["cloudwatch:DeleteAlarms", "cloudwatch:PutMetricAlarm", "cloudwatch:TagResource", "cloudwatch:UntagResource"]
    resources = local.apply_alarm_arns
  }

  statement {
    sid    = "NotificationTopics"
    effect = "Allow"
    actions = [
      "sns:CreateTopic",
      "sns:DeleteTopic",
      "sns:SetSubscriptionAttributes",
      "sns:SetTopicAttributes",
      "sns:Subscribe",
      "sns:TagResource",
      "sns:Unsubscribe",
      "sns:UntagResource",
    ]
    resources = local.apply_topic_arns
  }

  # Bucket configuration only — no object access on the listings buckets.
  statement {
    sid    = "ProjectBucketConfiguration"
    effect = "Allow"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucketPolicy",
      "s3:PutBucketOwnershipControls",
      "s3:PutBucketPolicy",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketTagging",
      "s3:PutBucketVersioning",
      "s3:PutEncryptionConfiguration",
    ]
    resources = concat(local.listings_bucket_arns, local.frontend_bucket_arns)
  }

  # The frontend asset buckets use force_destroy, so replacing one needs object
  # deletes. The listings buckets never get this.
  statement {
    sid       = "FrontendBucketLifecycle"
    effect    = "Allow"
    actions   = ["s3:DeleteBucket", "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:ListBucketVersions"]
    resources = concat(local.frontend_bucket_arns, [for arn in local.frontend_bucket_arns : "${arn}/*"])
  }

  # Secret metadata only; values are rotated by the owner (see README).
  statement {
    sid       = "SecretMetadata"
    effect    = "Allow"
    actions   = ["secretsmanager:CreateSecret", "secretsmanager:DeleteSecret", "secretsmanager:TagResource", "secretsmanager:UntagResource", "secretsmanager:UpdateSecret"]
    resources = local.apply_secret_arns
  }

  # CloudFront has no name-based ARNs and new resources get random IDs, so
  # writes cannot be scoped by name. Only this project uses CloudFront in the
  # account (checked 2026-11-10).
  statement {
    sid    = "CloudFront"
    effect = "Allow"
    actions = [
      "cloudfront:CreateCachePolicy",
      "cloudfront:CreateDistribution",
      "cloudfront:CreateOriginAccessControl",
      "cloudfront:DeleteCachePolicy",
      "cloudfront:DeleteDistribution",
      "cloudfront:DeleteOriginAccessControl",
      "cloudfront:TagResource",
      "cloudfront:UntagResource",
      "cloudfront:UpdateCachePolicy",
      "cloudfront:UpdateDistribution",
      "cloudfront:UpdateOriginAccessControl",
    ]
    resources = ["*"]
  }

  # ACM certificates for CloudFront live in us-east-1.
  statement {
    sid       = "AcmCertificates"
    effect    = "Allow"
    actions   = ["acm:AddTagsToCertificate", "acm:DeleteCertificate", "acm:RemoveTagsFromCertificate", "acm:RequestCertificate"]
    resources = ["arn:aws:acm:us-east-1:${local.account_id}:certificate/*"]
  }

  # DNS changes only for this project's records in leopoldwalther.com: the
  # frontend aliases and their ACM validation records.
  statement {
    sid       = "DnsRecords"
    effect    = "Allow"
    actions   = ["route53:ChangeResourceRecordSets"]
    resources = ["arn:aws:route53:::hostedzone/${local.route53_zone_id}"]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "route53:ChangeResourceRecordSetsNormalizedRecordNames"
      values   = ["vlc-market-pulse.leopoldwalther.com", "vlc-market-pulse-dev.leopoldwalther.com", "*.vlc-market-pulse.leopoldwalther.com", "*.vlc-market-pulse-dev.leopoldwalther.com"]
    }

    # ForAllValues passes vacuously when the key is missing; require it.
    condition {
      test     = "Null"
      variable = "route53:ChangeResourceRecordSetsNormalizedRecordNames"
      values   = ["false"]
    }
  }
}

# Deny guard rails (review M1). Evaluated before any Allow, so a later
# widening of the allow policy cannot switch them off.
data "aws_iam_policy_document" "github_actions_apply_deny" {

  statement {
    sid       = "DenyBoundaryRemoval"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }

  # Negated operators match when the key is missing, so this also denies
  # creating a role without any boundary.
  statement {
    sid       = "DenyForeignBoundary"
    effect    = "Deny"
    actions   = ["iam:CreateRole", "iam:PutRolePermissionsBoundary", "iam:PutRolePolicy"]
    resources = ["*"]

    condition {
      test     = "StringNotEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.boundary_policy_id]
    }
  }

  # Reads stay allowed: the environment stacks look the boundary up by name.
  statement {
    sid         = "DenyTouchingCiIdentity"
    effect      = "Deny"
    not_actions = ["iam:Get*", "iam:List*"]
    resources = [
      local.boundary_policy_id,
      "arn:aws:iam::${local.account_id}:role/github-actions-*",
      aws_iam_openid_connect_provider.github_actions.arn,
    ]
  }

  statement {
    sid    = "DenyManagedPolicies"
    effect = "Deny"
    actions = [
      "iam:AttachRolePolicy",
      "iam:CreatePolicy",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "DenySecretValues"
    effect    = "Deny"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"]
    resources = ["*"]
  }

  # CRITICAL: old state versions still contain plaintext credentials.
  statement {
    sid       = "DenyStateHistory"
    effect    = "Deny"
    actions   = ["s3:DeleteObjectVersion", "s3:GetObjectVersion"]
    resources = ["${local.state_bucket_arn}/*"]
  }

  statement {
    sid       = "DenySharedState"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = ["${local.state_bucket_arn}/vlc-state/shared/*"]
  }

  statement {
    sid       = "DenyStateBucketConfiguration"
    effect    = "Deny"
    actions   = ["s3:DeleteBucket", "s3:DeleteBucketPolicy", "s3:PutBucketPolicy", "s3:PutBucketVersioning", "s3:PutLifecycleConfiguration"]
    resources = [local.state_bucket_arn]
  }

  statement {
    sid       = "DenyListingsBucketDeletion"
    effect    = "Deny"
    actions   = ["s3:DeleteBucket"]
    resources = local.listings_bucket_arns
  }
}

resource "aws_iam_role" "github_actions_apply" {
  name                 = "github-actions-apply"
  assume_role_policy   = data.aws_iam_policy_document.github_actions_apply_trust.json
  description          = "Assumed by GitHub Actions dev/prod environment jobs via OIDC; terraform apply for the environment stacks."
  max_session_duration = 3600

  tags = {
    Name      = "github-actions-apply"
    ManagedBy = "terraform"
  }
}

# Allow and guard-rail documents of the apply role, attached as managed
# policies (inline policies share a 10,240-character budget per role).
locals {
  github_actions_apply_policies = {
    read        = { doc = data.aws_iam_policy_document.github_actions_apply_read.json, purpose = "configuration reads and environment state" }
    iam         = { doc = data.aws_iam_policy_document.github_actions_apply_iam.json, purpose = "boundary-guarded workload role writes" }
    compute     = { doc = data.aws_iam_policy_document.github_actions_apply_compute.json, purpose = "Lambda functions, layers and log groups" }
    platform    = { doc = data.aws_iam_policy_document.github_actions_apply_platform.json, purpose = "orchestration, SNS, S3, secret metadata, CloudFront, ACM, DNS" }
    guard-rails = { doc = data.aws_iam_policy_document.github_actions_apply_deny.json, purpose = "explicit deny guard rails" }
  }
}

resource "aws_iam_policy" "github_actions_apply" {
  for_each = local.github_actions_apply_policies

  name        = "github-actions-apply-${each.key}"
  description = "GitHub Actions apply role: ${each.value.purpose}."
  policy      = each.value.doc
}

resource "aws_iam_role_policy_attachment" "github_actions_apply" {
  for_each = aws_iam_policy.github_actions_apply

  role       = aws_iam_role.github_actions_apply.name
  policy_arn = each.value.arn
}
