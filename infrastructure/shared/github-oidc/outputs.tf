output "github_actions_deploy_role_arn" {
  description = "ARN of the IAM role assumed by GitHub Actions deploy workflows via OIDC. Set as the AWS_DEPLOY_ROLE_ARN variable (or secret) in GitHub repository settings."
  value       = aws_iam_role.github_actions_deploy.arn
}

output "workload_boundary_policy_arn" {
  description = "ARN of the permissions boundary every workload role in dev and prod must carry."
  value       = aws_iam_policy.vlc_workload_boundary.arn
}

output "github_actions_plan_role_arn" {
  description = "ARN of the read-only IAM role for pull-request terraform plans. Set as the AWS_PLAN_ROLE_ARN repository variable."
  value       = aws_iam_role.github_actions_plan.arn
}
