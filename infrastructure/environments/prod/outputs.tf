output "listings_bucket_name" {
  description = "Name of the S3 bucket for Idealista API data"
  value       = module.listings_bucket.listings_bucket_name
}

output "listings_bucket_arn" {
  description = "ARN of the S3 bucket for Idealista API data"
  value       = module.listings_bucket.listings_bucket_arn
}

output "idealista_secret_name_lvw" {
  description = "Name of the AWS Secrets Manager secret for LVW's Idealista credentials"
  value       = module.idealista_secrets.secret_name_lvw
}

output "idealista_secret_name_pmv" {
  description = "Name of the AWS Secrets Manager secret for PMV's Idealista credentials"
  value       = module.idealista_secrets.secret_name_pmv
}

output "idealista_secret_arn_lvw" {
  description = "ARN of the AWS Secrets Manager secret for LVW's Idealista credentials"
  value       = module.idealista_secrets.secret_arn_lvw
  sensitive   = true
}

output "idealista_secret_arn_pmv" {
  description = "ARN of the AWS Secrets Manager secret for PMV's Idealista credentials"
  value       = module.idealista_secrets.secret_arn_pmv
  sensitive   = true
}

output "lambda_function_name" {
  description = "Name of the Idealista collector Lambda function"
  value       = module.idealista_collector.function_name
}

output "lambda_function_arn" {
  description = "ARN of the Idealista collector Lambda function"
  value       = module.idealista_collector.function_arn
}

output "lambda_log_group_name" {
  description = "Name of the Lambda CloudWatch Log Group"
  value       = module.idealista_collector.log_group_name
}

output "lambda_event_rule_name" {
  description = "Name of the EventBridge rule triggering the Lambda"
  value       = module.idealista_collector.event_rule_name
}

output "cloudfront_url" {
  description = "Default CloudFront distribution URL (available immediately, before the custom domain is active)."
  value       = "https://${module.frontend.distribution_domain_name}"
}

output "custom_domain_url" {
  description = "Custom domain URL of the prod frontend (vlc-market-pulse.leopoldwalther.com)."
  value       = "https://${local.frontend_domain}"
}

output "frontend_asset_bucket_name" {
  description = "Name of the private S3 bucket holding frontend static assets. Used by the deploy workflow."
  value       = module.frontend.asset_bucket_name
}

output "frontend_distribution_id" {
  description = "CloudFront distribution ID. Used by the deploy workflow for cache invalidation."
  value       = module.frontend.distribution_id
}

output "pipeline_state_machine_arn" {
  description = "ARN of the medallion pipeline Step Functions state machine."
  value       = module.pipeline_orchestrator.state_machine_arn
}

output "pipeline_state_machine_name" {
  description = "Name of the medallion pipeline Step Functions state machine."
  value       = module.pipeline_orchestrator.state_machine_name
}
