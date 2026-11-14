#!/usr/bin/env bash
# One-off migration (FEATURE-019): copy the two Idealista credential sets of one
# environment from AWS Secrets Manager to SSM Parameter Store (SecureString,
# standard tier, AWS-managed key aws/ssm).
#
# Run by the OWNER on a local machine only. Never run it in CI or through an AI
# agent: it handles credential values.
#
# Value handling:
#   * a value is held in a shell variable and handed to the AWS CLI through
#     stdin — never as a command-line argument (visible in the process list)
#     and never written to disk;
#   * nothing but names and "match"/"MISMATCH" is printed. The copy is verified
#     by comparing SHA-256 digests of the secret and of the stored parameter.
#
# Idempotent: re-running overwrites the parameters with the current secrets.
#
# Usage: dev/tools/migrate_idealista_credentials.sh <dev|prod>
set -euo pipefail
# CRITICAL: command tracing would print the credential values.
set +x

environment="${1:-}"
case "${environment}" in
  dev | prod) ;;
  *)
    echo "Usage: $0 <dev|prod>" >&2
    exit 2
    ;;
esac

export AWS_REGION="${AWS_REGION:-eu-central-1}"

# Print the SHA-256 digest of stdin (macOS ships shasum, Linux sha256sum).
digest() {
  if command -v sha256sum > /dev/null 2>&1; then
    sha256sum | cut -d ' ' -f 1
  else
    shasum -a 256 | cut -d ' ' -f 1
  fi
}

failures=0
for credential_set in lvw pmv; do
  secret_id="${environment}/idealista/${credential_set}-api-credentials"
  parameter_name="/vlc-market-pulse/${environment}/idealista/${credential_set}-api-credentials"

  value="$(aws secretsmanager get-secret-value --secret-id "${secret_id}" \
    --query SecretString --output text)"
  if [[ -z "${value}" || "${value}" == "None" ]]; then
    echo "${secret_id}: secret has no string value" >&2
    exit 1
  fi

  # printf is a shell builtin, so the value never appears in a process list.
  printf '%s' "${value}" | aws ssm put-parameter \
    --name "${parameter_name}" \
    --description "Idealista API credentials (${credential_set}) for ${environment}; value managed outside Terraform" \
    --type SecureString \
    --tier Standard \
    --overwrite \
    --value file:///dev/stdin > /dev/null

  stored="$(aws ssm get-parameter --name "${parameter_name}" --with-decryption \
    --query Parameter.Value --output text)"

  if [[ "$(printf '%s' "${value}" | digest)" == "$(printf '%s' "${stored}" | digest)" ]]; then
    echo "${parameter_name}: match"
  else
    echo "${parameter_name}: MISMATCH" >&2
    failures=$((failures + 1))
  fi
  unset value stored
done

if [[ "${failures}" -ne 0 ]]; then
  echo "${failures} parameter(s) differ from their secret — do not cut over." >&2
  exit 1
fi

echo "Parameter metadata:"
aws ssm describe-parameters \
  --parameter-filters "Key=Name,Option=BeginsWith,Values=/vlc-market-pulse/${environment}/idealista/" \
  --query 'Parameters[].[Name,Type,Tier]' --output text
