variable "environment" {
  type        = string
  description = "Deployment environment (dev or prod)."
}

variable "listings_bucket_name" {
  type        = string
  description = "Name of the S3 bucket that holds gold/aggregations/latest.json (the listings bucket). Used as the second CloudFront origin so the frontend and its data are same-origin."
}

variable "listings_bucket_arn" {
  type        = string
  description = "ARN of the listings S3 bucket. Used to scope the OAC bucket policy for the data origin."
}

variable "listings_bucket_regional_domain" {
  type        = string
  description = "Regional domain name of the listings bucket (e.g. <bucket>.s3.<region>.amazonaws.com). Used as the CloudFront origin domain for the data behaviour."
}

variable "certificate_arn" {
  type        = string
  description = "ARN of the ACM certificate for the custom domain aliases. Must be in us-east-1 (CloudFront requirement). Issued per environment for that environment's domain only; pass the aws_acm_certificate_validation ARN so the distribution waits for validation."
}

variable "aliases" {
  type        = list(string)
  description = "Custom domain aliases for the CloudFront distribution (e.g. [\"vlc-market-pulse.leopoldwalther.com\"])."
  default     = []
}

variable "gold_prefix" {
  type        = string
  description = "S3 key prefix for gold aggregation objects served via the data origin behaviour."
  default     = "gold/aggregations"
}

variable "pipeline_health_prefix" {
  type        = string
  description = "S3 key prefix for pipeline health observer objects served via the data origin behaviour (review H2)."
  default     = "gold/pipeline_health"
}

variable "data_cache_ttl_seconds" {
  type        = number
  description = "Maximum CloudFront cache TTL for the gold data origin (latest.json). Short so chart data refreshes within one cycle."
  default     = 3600
}
