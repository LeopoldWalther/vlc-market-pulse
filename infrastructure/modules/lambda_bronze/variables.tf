variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string

  validation {
    condition     = can(regex("^(dev|staging|prod)$", var.environment))
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "s3_bucket_name" {
  description = "Name of the S3 bucket for listings storage"
  type        = string
}

variable "s3_bucket_arn" {
  description = "ARN of the S3 bucket for listings storage"
  type        = string
}

variable "secret_name_lvw" {
  description = "Name of the Secrets Manager secret for lvw credentials"
  type        = string
}

variable "secret_arn_lvw" {
  description = "ARN of the Secrets Manager secret for lvw credentials"
  type        = string
}

variable "secret_name_pmv" {
  description = "Name of the Secrets Manager secret for pmv credentials"
  type        = string
}

variable "secret_arn_pmv" {
  description = "ARN of the Secrets Manager secret for pmv credentials"
  type        = string
}

variable "test_mode" {
  description = <<-EOT
    When true, the scheduled EventBridge trigger invokes the collector with
    {"test_mode": true}, limiting it to 1 page per operation (2 API calls total).
    Use in dev to stay within Idealista API limits. Defaults to false (full weekly collection).
  EOT
  type        = bool
  default     = false
}

variable "create_schedule" {
  description = <<-EOT
    When true (default), an EventBridge rule triggers this Lambda on the
    module's built-in cron schedule. Set to false when the pipeline_orchestrator
    module owns the trigger (Step Functions) so the per-Lambda schedule is not
    created alongside the orchestrator schedule.
  EOT
  type        = bool
  default     = true
}
