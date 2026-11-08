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
    sid    = "ReadResourceConfiguration"
    effect = "Allow"
    actions = [
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
    resources = ["*"]
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
