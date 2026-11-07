variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string

  validation {
    condition     = can(regex("^(dev|staging|prod)$", var.environment))
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "aws_region" {
  description = "AWS region used to build region-specific ARNs (e.g. managed layer ARN)"
  type        = string
}

variable "s3_bucket_name" {
  description = "Name of the shared S3 bucket (contains silver/ and gold/ prefixes)"
  type        = string
}

variable "s3_bucket_arn" {
  description = "ARN of the shared S3 bucket"
  type        = string
}

variable "sns_topic_arn" {
  description = "ARN of the SNS topic used for Lambda error alarms"
  type        = string
}

variable "pandas_layer_arn" {
  description = <<-EOT
    ARN of the AWS-managed AWSSDKPandas-Python312 Lambda layer for the target
    region. Must be provided by the calling environment so the module stays
    region-agnostic and deployable without modification. Obtain the correct ARN
    from: https://aws-sdk-pandas.readthedocs.io/en/stable/layers.html
  EOT
  type        = string
}

variable "ratio_min_count" {
  description = <<-EOT
    Minimum number of listings per operation side (sale or rent) required for a
    neighborhood to appear in the rent-vs-sale ratio datasets. Neighborhoods
    with fewer listings are excluded to avoid unreliable ratios from sparse data.
    Passed to the Lambda as the RATIO_MIN_COUNT environment variable.
  EOT
  type        = number
  default     = 5
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

variable "permissions_boundary_arn" {
  description = "ARN of the IAM permissions boundary (vlc-workload-boundary) attached to this module's role(s)."
  type        = string
}
