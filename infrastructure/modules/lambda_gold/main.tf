terraform {
  required_version = ">= 1.2, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.0"
    }
  }
}

# ---------------------------------------------------------------------------
# Deployment package — bundles the data_processing modules at the zip root
# (handler + pure aggregation helpers, flat imports preserved) plus the shared
# src/etl/common/ package (edge Protocols + AWS adapters) under common/ so
# `from common... import ...` resolves at Lambda cold start.
#
# fileset() keeps this future-proof: new top-level .py modules in
# data_processing/ or common/ are picked up automatically (tests live in
# tests/ subdirectories and are therefore never matched).
# ---------------------------------------------------------------------------
locals {
  etl_root = "${path.module}/../../../src/etl"
  # explore_bronze.py is a local exploration script, never deployed.
  processing_files = [
    for f in fileset("${local.etl_root}/data_processing", "*.py")
    : f if f != "explore_bronze.py"
  ]
  common_files = fileset("${local.etl_root}/common", "*.py")
}

data "archive_file" "gold_lambda_zip" {
  type        = "zip"
  output_path = "${path.module}/gold_aggregation_lambda.zip"

  dynamic "source" {
    for_each = local.processing_files
    content {
      content  = file("${local.etl_root}/data_processing/${source.value}")
      filename = source.value
    }
  }

  dynamic "source" {
    for_each = local.common_files
    content {
      content  = file("${local.etl_root}/common/${source.value}")
      filename = "common/${source.value}"
    }
  }
}

# ---------------------------------------------------------------------------
# IAM role
# ---------------------------------------------------------------------------
resource "aws_iam_role" "gold_lambda_role" {
  name = "${var.environment}-gold-aggregator-lambda-role"
  # Caps what any inline policy on this role can grant (see shared/github-oidc).
  permissions_boundary = var.permissions_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "${var.environment}-gold-aggregator-lambda-role"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}

# CloudWatch Logs write access
resource "aws_iam_role_policy" "gold_lambda_logging" {
  name = "${var.environment}-gold-aggregator-logging"
  role = aws_iam_role.gold_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/${var.environment}-gold-aggregator:*"
      }
    ]
  })
}

# S3 access — read silver/idealista/* + write gold/aggregations/* (least privilege)
resource "aws_iam_role_policy" "gold_lambda_s3" {
  name = "${var.environment}-gold-aggregator-s3"
  role = aws_iam_role.gold_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = var.s3_bucket_arn
        Condition = {
          StringLike = {
            "s3:prefix" = ["silver/idealista/*", "gold/aggregations/*"]
          }
        }
      },
      {
        Sid    = "ReadSilver"
        Effect = "Allow"
        Action = ["s3:GetObject"]
        # Scoped to the silver/idealista/ prefix only — no bronze or gold read.
        Resource = "${var.s3_bucket_arn}/silver/idealista/*"
      },
      {
        Sid    = "WriteGold"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:PutObjectAcl"]
        # Write only within the gold/aggregations/ prefix — cannot touch silver or bronze.
        Resource = "${var.s3_bucket_arn}/gold/aggregations/*"
      },
    ]
  })
}

# SNS publish access — error notifications
resource "aws_iam_role_policy" "gold_lambda_sns" {
  name = "${var.environment}-gold-aggregator-sns"
  role = aws_iam_role.gold_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = var.sns_topic_arn
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# Lambda function
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "gold_aggregator" {
  filename         = data.archive_file.gold_lambda_zip.output_path
  function_name    = "${var.environment}-gold-aggregator"
  role             = aws_iam_role.gold_lambda_role.arn
  handler          = "gold_aggregation_lambda.lambda_handler"
  source_code_hash = data.archive_file.gold_lambda_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 300 # 5 minutes — well within Lambda max; aggregating small data
  memory_size      = 512 # Needed for pandas/pyarrow cold start via managed layer

  # pandas + pyarrow via AWS-managed layer (AWSSDKPandas-Python312).
  # The ARN is region-specific and passed in as a variable so this module
  # remains region-agnostic and deployable without modification.
  layers = [var.pandas_layer_arn]

  environment {
    variables = {
      S3_BUCKET       = var.s3_bucket_name
      SILVER_PREFIX   = "silver/idealista"
      GOLD_PREFIX     = "gold/aggregations"
      RATIO_MIN_COUNT = tostring(var.ratio_min_count)
      SNS_TOPIC_ARN   = var.sns_topic_arn
    }
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name        = "${var.environment}-gold-aggregator"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}

# ---------------------------------------------------------------------------
# CloudWatch Log Group (30-day retention, consistent with other Lambdas)
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "gold_lambda_logs" {
  name              = "/aws/lambda/${aws_lambda_function.gold_aggregator.function_name}"
  retention_in_days = 30

  tags = {
    Name        = "${var.environment}-gold-aggregator-logs"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}

# ---------------------------------------------------------------------------
# CloudWatch Alarm — trigger SNS on Lambda errors
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "gold_lambda_errors" {
  alarm_name          = "${var.environment}-gold-aggregator-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 3600 # 1 hour — alarm fires if any error in the hour after the schedule
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Gold aggregation Lambda raised at least one error"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.gold_aggregator.function_name
  }

  alarm_actions = [var.sns_topic_arn]

  tags = {
    Name        = "${var.environment}-gold-aggregator-errors"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}

# ---------------------------------------------------------------------------
# EventBridge — scheduled trigger: every Sunday at 12:45 UTC
# (45 min after the bronze collector, 15 min after silver, ensuring silver is ready)
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "gold_weekly_trigger" {
  count = var.create_schedule ? 1 : 0

  name                = "${var.environment}-gold-aggregator-weekly"
  description         = "Trigger gold aggregation Lambda weekly, 15 min after the silver cleaner"
  schedule_expression = "cron(45 12 ? * SUN *)"

  tags = {
    Name        = "${var.environment}-gold-aggregator-weekly"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}

resource "aws_cloudwatch_event_target" "gold_lambda_target" {
  count = var.create_schedule ? 1 : 0

  rule      = aws_cloudwatch_event_rule.gold_weekly_trigger[0].name
  target_id = "GoldAggregatorLambda"
  arn       = aws_lambda_function.gold_aggregator.arn
}

resource "aws_lambda_permission" "allow_eventbridge_gold" {
  count = var.create_schedule ? 1 : 0

  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gold_aggregator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.gold_weekly_trigger[0].arn
}
