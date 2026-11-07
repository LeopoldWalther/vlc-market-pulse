#!/usr/bin/env bash
# Print the sorted, unique list of IAM actions granted by the workload roles
# defined under infrastructure/modules/.
#
# The permissions boundary (infrastructure/shared/github-oidc, vlc-workload-boundary)
# must allow every action printed here, otherwise a workload role silently
# loses a permission once the boundary is attached. Re-run this after changing
# any aws_iam_role_policy and compare with the boundary.
#
# Usage: dev/tools/iam_action_inventory.sh [--check]
#   --check  additionally simulate every action against the deployed boundary
#            policy (needs AWS credentials) and exit non-zero on any denial.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
modules_dir="${repo_root}/infrastructure/modules"

# Actions are "service:PascalCaseName" (or a wildcard). Condition keys such as
# "s3:prefix" or "cloudwatch:namespace" start lower-case after the colon and are
# skipped. sts:AssumeRole only appears in trust policies, never in a role's own
# permissions, so it is excluded as well.
actions="$(grep -rhoE '"[a-z0-9-]+:([A-Z][A-Za-z]*|\*)"' "${modules_dir}" --include='*.tf' \
  | tr -d '"' \
  | grep -vx 'sts:AssumeRole' \
  | sort -u)"

echo "${actions}"

if [[ "${1:-}" != "--check" ]]; then
  exit 0
fi

account_id="$(aws sts get-caller-identity --query Account --output text)"
policy_arn="arn:aws:iam::${account_id}:policy/vlc-workload-boundary"
version="$(aws iam get-policy --policy-arn "${policy_arn}" --query 'Policy.DefaultVersionId' --output text)"
document="$(aws iam get-policy-version --policy-arn "${policy_arn}" --version-id "${version}" \
  --query 'PolicyVersion.Document' --output json)"

# Simulate against a real dev resource of this project per service, so the
# check also proves the boundary's resource scoping covers the workload.
region="eu-central-1"
sample_resource() {
  case "$1" in
    logs:CreateLogGroup | logs:CreateLogStream | logs:PutLogEvents | logs:StartQuery)
      echo "arn:aws:logs:${region}:${account_id}:log-group:/aws/lambda/dev-idealista-collector:*" ;;
    s3:ListBucket) echo "arn:aws:s3:::dev-vlc-real-estate-analytics-listings" ;;
    s3:*) echo "arn:aws:s3:::dev-vlc-real-estate-analytics-listings/silver/sample.json" ;;
    secretsmanager:*)
      echo "arn:aws:secretsmanager:${region}:${account_id}:secret:dev/idealista/lvw-api-credentials-AbCdEf" ;;
    sns:*) echo "arn:aws:sns:${region}:${account_id}:dev-idealista-notifications" ;;
    lambda:*) echo "arn:aws:lambda:${region}:${account_id}:function:dev-silver-cleaner" ;;
    states:*) echo "arn:aws:states:${region}:${account_id}:stateMachine:dev-medallion-pipeline" ;;
    *) echo "*" ;;
  esac
}

failed=0
while read -r action; do
  decision="$(aws iam simulate-custom-policy --policy-input-list "${document}" \
    --action-names "${action}" --resource-arns "$(sample_resource "${action}")" \
    --query 'EvaluationResults[0].EvalDecision' --output text)"
  printf '%-40s %s\n' "${action}" "${decision}" >&2
  [[ "${decision}" == "allowed" ]] || failed=1
done <<< "${actions}"

exit "${failed}"
