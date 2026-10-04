module "listings_bucket" {
  source = "../../modules/s3"

  environment = var.environment
}

module "idealista_secrets" {
  source = "../../modules/secrets"

  environment              = var.environment
  idealista_api_key_lvw    = var.idealista_api_key_lvw
  idealista_api_secret_lvw = var.idealista_api_secret_lvw
  idealista_api_key_pmv    = var.idealista_api_key_pmv
  idealista_api_secret_pmv = var.idealista_api_secret_pmv
}

module "idealista_notifications" {
  source = "../../modules/sns"

  environment        = var.environment
  notification_email = var.notification_email
}

module "idealista_collector" {
  source = "../../modules/lambda_bronze"

  environment     = var.environment
  aws_region      = var.aws_region
  s3_bucket_name  = module.listings_bucket.listings_bucket_name
  s3_bucket_arn   = module.listings_bucket.listings_bucket_arn
  secret_name_lvw = module.idealista_secrets.secret_name_lvw
  secret_arn_lvw  = module.idealista_secrets.secret_arn_lvw
  secret_name_pmv = module.idealista_secrets.secret_name_pmv
  secret_arn_pmv  = module.idealista_secrets.secret_arn_pmv
  sns_topic_arn   = module.idealista_notifications.topic_arn

  # CRITICAL: the pipeline_orchestrator module below owns the single weekly
  # trigger. Disabling this prevents a double-invocation on Sunday.
  create_schedule = false
}

module "silver_cleaner" {
  source = "../../modules/lambda_silver"

  environment      = var.environment
  aws_region       = var.aws_region
  s3_bucket_name   = module.listings_bucket.listings_bucket_name
  s3_bucket_arn    = module.listings_bucket.listings_bucket_arn
  sns_topic_arn    = module.idealista_notifications.topic_arn
  pandas_layer_arn = var.pandas_layer_arn

  # Schedule owned by pipeline_orchestrator.
  create_schedule = false
}

module "gold_aggregator" {
  source = "../../modules/lambda_gold"

  environment      = var.environment
  aws_region       = var.aws_region
  s3_bucket_name   = module.listings_bucket.listings_bucket_name
  s3_bucket_arn    = module.listings_bucket.listings_bucket_arn
  sns_topic_arn    = module.idealista_notifications.topic_arn
  pandas_layer_arn = var.pandas_layer_arn
  ratio_min_count  = 5

  # Schedule owned by pipeline_orchestrator.
  create_schedule = false
}

# ---------------------------------------------------------------------------
# Pipeline health observer — independent Lambda that monitors the 3 pipeline
# Lambdas (bronze/silver/gold) and writes gold/pipeline_health/latest.json.
# Deliberately NOT wired into the Step Functions state machine below: it
# observes the pipeline on its own weekly EventBridge schedule and must keep
# working even if the orchestrated pipeline run itself fails.
# ---------------------------------------------------------------------------
module "pipeline_health" {
  source = "../../modules/lambda_pipeline_health"

  environment    = var.environment
  aws_region     = var.aws_region
  s3_bucket_name = module.listings_bucket.listings_bucket_name
  s3_bucket_arn  = module.listings_bucket.listings_bucket_arn
  pipeline_function_names = [
    module.idealista_collector.function_name,
    module.silver_cleaner.function_name,
    module.gold_aggregator.function_name,
  ]
}

# ---------------------------------------------------------------------------
# Pipeline orchestrator — Step Functions state machine that runs
# bronze -> silver -> gold in sequence every Sunday at 12:00 UTC.
#
# The three per-Lambda EventBridge schedules above are disabled
# (create_schedule = false) so the orchestrator is the single trigger.
# ---------------------------------------------------------------------------
module "pipeline_orchestrator" {
  source = "../../modules/pipeline_orchestrator"

  environment         = var.environment
  aws_region          = var.aws_region
  bronze_function_arn = module.idealista_collector.function_arn
  silver_function_arn = module.silver_cleaner.function_arn
  gold_function_arn   = module.gold_aggregator.function_arn
  sns_topic_arn       = module.idealista_notifications.topic_arn

  # prod collects the full result set (all pages) on every run.
  test_mode = false
}

# ---------------------------------------------------------------------------
# Prod frontend domain. The certificate below covers this name only.
# ---------------------------------------------------------------------------
locals {
  frontend_domain = "vlc-market-pulse.leopoldwalther.com"
}

# ---------------------------------------------------------------------------
# Hosted zone — read-only lookup. leopoldwalther.com (zone, apex, CAA and the
# apex validation record) is owned by the leopoldwalther-website repository;
# this stack only writes records under its own subdomain.
# ---------------------------------------------------------------------------
data "aws_route53_zone" "root" {
  name         = "leopoldwalther.com"
  private_zone = false
}

# ---------------------------------------------------------------------------
# ACM certificate for the prod frontend domain only — issued in us-east-1
# (CloudFront requirement). create_before_destroy keeps the distribution on a
# valid certificate during any rotation.
# ---------------------------------------------------------------------------
resource "aws_acm_certificate" "frontend" {
  provider = aws.us_east_1

  domain_name       = local.frontend_domain
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "frontend_cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.frontend.domain_validation_options :
    dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  }

  zone_id = data.aws_route53_zone.root.zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.record]
  ttl     = 60
}

# Waits until ACM reports the certificate as issued. CloudFront consumes the
# ARN from this resource, so the distribution never switches to a certificate
# that is still pending validation.
resource "aws_acm_certificate_validation" "frontend" {
  provider = aws.us_east_1

  certificate_arn         = aws_acm_certificate.frontend.arn
  validation_record_fqdns = [for r in aws_route53_record.frontend_cert_validation : r.fqdn]
}

# ---------------------------------------------------------------------------
# Frontend module — private S3 asset bucket + CloudFront distribution.
# ---------------------------------------------------------------------------
module "frontend" {
  source = "../../modules/frontend"

  environment                     = var.environment
  listings_bucket_name            = module.listings_bucket.listings_bucket_name
  listings_bucket_arn             = module.listings_bucket.listings_bucket_arn
  listings_bucket_regional_domain = module.listings_bucket.listings_bucket_regional_domain
  certificate_arn                 = aws_acm_certificate_validation.frontend.certificate_arn
  aliases                         = [local.frontend_domain]
}

# ---------------------------------------------------------------------------
# Route 53 alias records — A + AAAA for the prod frontend domain
# pointing at the CloudFront distribution in the existing hosted zone.
# ---------------------------------------------------------------------------
resource "aws_route53_record" "frontend_a" {
  zone_id = data.aws_route53_zone.root.zone_id
  name    = local.frontend_domain
  type    = "A"

  alias {
    name                   = module.frontend.distribution_domain_name
    zone_id                = "Z2FDTNDATAQYW2" # CloudFront hosted zone ID (global constant)
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "frontend_aaaa" {
  zone_id = data.aws_route53_zone.root.zone_id
  name    = local.frontend_domain
  type    = "AAAA"

  alias {
    name                   = module.frontend.distribution_domain_name
    zone_id                = "Z2FDTNDATAQYW2" # CloudFront hosted zone ID (global constant)
    evaluate_target_health = false
  }
}
