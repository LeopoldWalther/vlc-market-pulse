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
#   • Lambda collector functions (dev + prod): UpdateFunctionCode
#   • Terraform remote state bucket: GetObject, ListBucket (for terraform output)
#   • Terraform state lock table: DynamoDB GetItem, PutItem, DeleteItem
#     (project currently uses use_lockfile = true / S3 native locking; included
#     for forward-compatibility with any future DynamoDB-backed lock table)
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

  # Lambda — update function code for the collector functions (dev + prod).
  statement {
    sid     = "LambdaUpdateFunctionCode"
    effect  = "Allow"
    actions = ["lambda:UpdateFunctionCode"]
    resources = [
      "arn:aws:lambda:eu-central-1:${data.aws_caller_identity.current.account_id}:function:dev-idealista-collector",
      "arn:aws:lambda:eu-central-1:${data.aws_caller_identity.current.account_id}:function:prod-idealista-collector",
    ]
  }

  # Terraform remote state bucket — needed for `terraform output` in CI steps.
  statement {
    sid     = "TerraformStateGetObject"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "arn:aws:s3:::vlc-real-estate-analytics-tf-state/*",
    ]
  }

  statement {
    sid       = "TerraformStateBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::vlc-real-estate-analytics-tf-state"]
  }

  # Terraform state lock table — DynamoDB locking permissions.
  # The project uses use_lockfile = true (S3 native locking) so no DynamoDB
  # table is currently provisioned; included for forward-compatibility.
  statement {
    sid    = "TerraformStateLock"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
    ]
    resources = [
      "arn:aws:dynamodb:eu-central-1:${data.aws_caller_identity.current.account_id}:table/vlc-real-estate-analytics-tf-lock",
    ]
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
