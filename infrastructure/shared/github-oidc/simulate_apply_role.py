"""
Simulate the github-actions-apply permission matrix (FEATURE-018 task 18.7).

Usage:
    terraform plan -out=apply.plan && terraform show -json apply.plan > apply.json
    python simulate_apply_role.py --plan apply.json      # planned policy documents
    python simulate_apply_role.py --role github-actions-apply   # deployed role

Prints a Markdown table (the one in documentation/CI_CD.md) and exits non-zero
when any case does not match its expected decision.
"""

import json
import subprocess
import sys
from typing import List, Tuple

ACCT = "063096286047"
BOUNDARY = f"arn:aws:iam::{ACCT}:policy/vlc-workload-boundary"
STATE = "arn:aws:s3:::vlc-real-estate-analytics-tf-state"


def R(name: str) -> str:
    """Return the IAM role ARN for ``name``."""
    return f"arn:aws:iam::{ACCT}:role/{name}"


def FN(name: str) -> str:
    """Return the Lambda function ARN for ``name``."""
    return f"arn:aws:lambda:eu-central-1:{ACCT}:function:{name}"


BCTX = [
    f"ContextKeyName=iam:PermissionsBoundary,ContextKeyValues={BOUNDARY},ContextKeyType=string"
]

# (action, resource ARN, --context-entries, expected)
# expected: "allow", "deny" (implicit or explicit) or "explicit-deny" (an
# explicit Deny statement must match — an absent Allow is not enough).
CASES: List[Tuple[str, str, List[str], str]] = [
    ("lambda:UpdateFunctionConfiguration", FN("prod-idealista-collector"), [], "allow"),
    ("lambda:UpdateFunctionCode", FN("dev-silver-cleaner"), [], "allow"),
    (
        "states:UpdateStateMachine",
        f"arn:aws:states:eu-central-1:{ACCT}:stateMachine:dev-medallion-pipeline",
        [],
        "allow",
    ),
    ("iam:PutRolePolicy", R("dev-silver-cleaner-lambda-role"), BCTX, "allow"),
    ("iam:CreateRole", R("dev-silver-cleaner-lambda-role"), BCTX, "allow"),
    (
        "iam:PassRole",
        R("prod-medallion-pipeline-sfn-role"),
        [
            "ContextKeyName=iam:PassedToService,ContextKeyValues=states.amazonaws.com,ContextKeyType=string"
        ],
        "allow",
    ),
    ("iam:GetPolicy", BOUNDARY, [], "allow"),
    ("s3:PutObject", f"{STATE}/vlc-state/prod/terraform.tfstate.tflock", [], "allow"),
    (
        "s3:ListBucket",
        "arn:aws:s3:::prod-vlc-real-estate-analytics-listings",
        [],
        "allow",
    ),
    (
        "route53:ChangeResourceRecordSets",
        "arn:aws:route53:::hostedzone/Z001078818QR783M1TOKB",
        [
            "ContextKeyName=route53:ChangeResourceRecordSetsNormalizedRecordNames,ContextKeyValues=vlc-market-pulse-dev.leopoldwalther.com,ContextKeyType=stringList"
        ],
        "allow",
    ),
    ("lambda:InvokeFunction", FN("dev-silver-cleaner"), [], "allow"),
    (
        "states:StartExecution",
        f"arn:aws:states:eu-central-1:{ACCT}:stateMachine:dev-medallion-pipeline",
        [],
        "allow",
    ),
    ("iam:CreateRole", R("dev-silver-cleaner-lambda-role"), [], "deny"),
    ("iam:CreateRole", R("dev-x"), BCTX, "deny"),
    ("iam:PutRolePolicy", R("dev-vlc-market-oracle-collector-role"), BCTX, "deny"),
    ("iam:UpdateAssumeRolePolicy", R("github-actions-deploy"), [], "deny"),
    ("iam:CreatePolicyVersion", BOUNDARY, [], "deny"),
    (
        "iam:DeleteRolePermissionsBoundary",
        R("dev-silver-cleaner-lambda-role"),
        [],
        "deny",
    ),
    ("iam:AttachRolePolicy", R("dev-silver-cleaner-lambda-role"), BCTX, "deny"),
    (
        "iam:PassRole",
        R("dev-silver-cleaner-lambda-role"),
        [
            "ContextKeyName=iam:PassedToService,ContextKeyValues=ec2.amazonaws.com,ContextKeyType=string"
        ],
        "deny",
    ),
    (
        "secretsmanager:GetSecretValue",
        f"arn:aws:secretsmanager:eu-central-1:{ACCT}:secret:prod/idealista/lvw-api-credentials-x",
        [],
        "deny",
    ),
    (
        "secretsmanager:PutSecretValue",
        f"arn:aws:secretsmanager:eu-central-1:{ACCT}:secret:prod/idealista/lvw-api-credentials-x",
        [],
        "deny",
    ),
    (
        "ssm:GetParameter",
        f"arn:aws:ssm:eu-central-1:{ACCT}:parameter/vlc-market-pulse/prod/idealista/lvw-api-credentials",
        [],
        "explicit-deny",
    ),
    (
        "ssm:PutParameter",
        f"arn:aws:ssm:eu-central-1:{ACCT}:parameter/vlc-market-pulse/prod/idealista/lvw-api-credentials",
        [],
        "explicit-deny",
    ),
    (
        "s3:PutObject",
        f"{STATE}/vlc-state/shared/github-oidc/terraform.tfstate",
        [],
        "deny",
    ),
    ("s3:GetObjectVersion", f"{STATE}/vlc-state/prod/terraform.tfstate", [], "deny"),
    (
        "s3:DeleteBucket",
        "arn:aws:s3:::prod-vlc-real-estate-analytics-listings",
        [],
        "deny",
    ),
    (
        "s3:GetObject",
        "arn:aws:s3:::prod-vlc-real-estate-analytics-listings/bronze/x.json",
        [],
        "deny",
    ),
    ("lambda:InvokeFunction", FN("prod-idealista-collector"), [], "deny"),
    ("lambda:InvokeFunction", FN("prod-silver-cleaner"), [], "deny"),
    ("lambda:InvokeFunction", FN("prod-pipeline-health"), [], "deny"),
    (
        "states:StartExecution",
        f"arn:aws:states:eu-central-1:{ACCT}:stateMachine:prod-medallion-pipeline",
        [],
        "deny",
    ),
    ("lambda:UpdateFunctionCode", FN("prod-vlc-market-oracle-budget-gate"), [], "deny"),
    (
        "route53:ChangeResourceRecordSets",
        "arn:aws:route53:::hostedzone/Z001078818QR783M1TOKB",
        [
            "ContextKeyName=route53:ChangeResourceRecordSetsNormalizedRecordNames,ContextKeyValues=www.leopoldwalther.com,ContextKeyType=stringList"
        ],
        "deny",
    ),
]


def main() -> int:
    """
    Run every case against the planned or the deployed policies.

    Returns:
        0 when every decision matches, 1 otherwise.
    """
    if len(sys.argv) != 3 or sys.argv[1] not in ("--plan", "--role"):
        print(__doc__)
        return 2
    mode, src = sys.argv[1], sys.argv[2]
    if mode == "--plan":
        with open(src) as plan_file:
            plan = json.load(plan_file)
        docs = [
            rc["change"]["after"]["policy"]
            for rc in plan["resource_changes"]
            if rc["type"] == "aws_iam_policy"
            and "github_actions_apply" in rc["address"]
        ]
        base = ["aws", "iam", "simulate-custom-policy", "--policy-input-list", *docs]
    else:
        base = [
            "aws",
            "iam",
            "simulate-principal-policy",
            "--policy-source-arn",
            R(src),
        ]
    failures = 0
    print("| Action | Resource | Expected | Result |\n|---|---|---|---|")
    for action, res, ctx, exp in CASES:
        cmd = base + [
            "--action-names",
            action,
            "--resource-arns",
            res,
            "--query",
            "EvaluationResults[0].EvalDecision",
            "--output",
            "text",
        ]
        if ctx:
            cmd += ["--context-entries", *ctx]
        out = (
            subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
            or "ERROR"
        )
        if exp == "explicit-deny":
            ok = out == "explicitDeny"
        else:
            ok = (out == "allowed") == (exp == "allow")
        failures += not ok
        print(
            f"| `{action}` | `{res.split(':')[-1]}`{' (+ctx)' if ctx else ''} | {exp} | {out}{'' if ok else ' ❌'} |"
        )
    print(f"\nfailures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
