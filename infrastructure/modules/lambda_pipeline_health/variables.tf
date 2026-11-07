variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string

  validation {
    condition     = can(regex("^(dev|staging|prod)$", var.environment))
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "aws_region" {
  description = "AWS region used to build region-specific ARNs (e.g. pipeline Lambda log group ARNs)"
  type        = string
}

variable "s3_bucket_name" {
  description = "Name of the shared S3 bucket (contains the gold/ prefix)"
  type        = string
}

variable "s3_bucket_arn" {
  description = "ARN of the shared S3 bucket"
  type        = string
}

variable "pipeline_function_names" {
  description = <<-EOT
    Names of the 3 pipeline Lambda functions (bronze, silver, gold) this
    observer monitors via CloudWatch Logs Insights (execution success/
    duration checks) and reports on in gold/pipeline_health/latest.json.
    Passed to the Lambda as the comma-separated PIPELINE_FUNCTION_NAMES
    environment variable.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.pipeline_function_names) == 3
    error_message = "Exactly 3 pipeline Lambda function names (bronze, silver, gold) must be provided."
  }
}

variable "create_schedule" {
  description = <<-EOT
    When true (default), an EventBridge rule triggers this Lambda on the
    module's built-in cron schedule. This observer Lambda is intentionally
    NOT part of the Step Functions pipeline orchestration — it reports on
    the pipeline, it does not participate in it — so this flag is not tied
    to the pipeline_orchestrator module the way the bronze/silver/gold
    per-Lambda schedules are.
  EOT
  type        = bool
  default     = true
}

variable "permissions_boundary_arn" {
  description = "ARN of the IAM permissions boundary (vlc-workload-boundary) attached to this module's role(s)."
  type        = string
}
