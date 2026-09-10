module "listings_bucket" {
  source = "../../modules/s3"

  environment = var.environment
}

# Frontend custom domain. dev uses its own subdomain so it never collides with
# the prod deployment (which claims vlc-market-pulse.leopoldwalther.com). The
# wildcard ACM cert (*.leopoldwalther.com) covers this name.
locals {
  frontend_domain = "vlc-market-pulse-dev.leopoldwalther.com"
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

  # Dev only collects 1 page per operation per week to stay within API limits.
  test_mode = true

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

  # Pass test_mode=true to the bronze Lambda so dev runs collect only 1 page.
  test_mode = true
}

# ---------------------------------------------------------------------------
# Shared DNS remote state — reads zone_id + certificate_arn from the
# infrastructure/shared/dns stack. That stack must be applied ONCE before
# this environment is planned or applied.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "dns" {
  backend = "s3"
  config = {
    bucket       = "vlc-real-estate-analytics-tf-state"
    key          = "vlc-state/shared/dns/terraform.tfstate"
    region       = "eu-central-1"
    encrypt      = true
    use_lockfile = true
  }
}

# ---------------------------------------------------------------------------
# Frontend module — private S3 asset bucket + CloudFront distribution.
# Certificate and zone come from the shared/dns remote state.
# prod wiring is intentionally deferred to FEATURE-006.
# ---------------------------------------------------------------------------
module "frontend" {
  source = "../../modules/frontend"

  environment                     = var.environment
  listings_bucket_name            = module.listings_bucket.listings_bucket_name
  listings_bucket_arn             = module.listings_bucket.listings_bucket_arn
  listings_bucket_regional_domain = module.listings_bucket.listings_bucket_regional_domain
  certificate_arn                 = data.terraform_remote_state.dns.outputs.certificate_arn
  aliases                         = [local.frontend_domain]
}

# ---------------------------------------------------------------------------
# Route 53 alias records — A + AAAA for the dev frontend domain
# pointing at the CloudFront distribution in the existing hosted zone.
# ---------------------------------------------------------------------------
resource "aws_route53_record" "frontend_a" {
  zone_id = data.terraform_remote_state.dns.outputs.zone_id
  name    = local.frontend_domain
  type    = "A"

  alias {
    name                   = module.frontend.distribution_domain_name
    zone_id                = "Z2FDTNDATAQYW2" # CloudFront hosted zone ID (global constant)
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "frontend_aaaa" {
  zone_id = data.terraform_remote_state.dns.outputs.zone_id
  name    = local.frontend_domain
  type    = "AAAA"

  alias {
    name                   = module.frontend.distribution_domain_name
    zone_id                = "Z2FDTNDATAQYW2" # CloudFront hosted zone ID (global constant)
    evaluate_target_health = false
  }
}
