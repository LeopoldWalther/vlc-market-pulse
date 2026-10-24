variable "environment" {
  description = "Environment name (dev, staging, prod). Used to namespace all resources."
  type        = string

  validation {
    condition     = can(regex("^(dev|staging|prod)$", var.environment))
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "aws_region" {
  description = "AWS region. Required to construct CloudWatch Logs delivery ARNs."
  type        = string
}

variable "bronze_function_arn" {
  description = "ARN of the bronze (Idealista collector) Lambda function."
  type        = string
}

variable "silver_function_arn" {
  description = "ARN of the silver (cleaning) Lambda function."
  type        = string
}

variable "gold_function_arn" {
  description = "ARN of the gold (aggregation) Lambda function."
  type        = string
}

variable "sns_topic_arn" {
  description = "ARN of the SNS topic used to publish the per-run pipeline summary (success or failure)."
  type        = string
}

variable "test_mode" {
  description = <<-EOT
    When true, the state machine passes test_mode=true to the bronze Lambda so
    the collector fetches only 1 page per operation (dev API-limit guard).
    Set to false for full weekly collection in prod.
  EOT
  type        = bool
  default     = false
}
