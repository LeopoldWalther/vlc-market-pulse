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
# Deployment package — bundles the pipeline_health modules at the zip root
# (handler + health checks + aggregator, flat imports preserved) plus the
# shared src/etl/common/ package (edge Protocols + AWS adapters) under
# common/ so `from common... import ...` resolves at Lambda cold start.
#
# fileset() keeps this future-proof: new top-level .py modules in
# pipeline_health/ or common/ are picked up automatically (tests live in
# tests/ subdirectories and are therefore never matched — fileset("*.py")
# is non-recursive, so it never descends into pipeline_health/tests/ or
# common/tests/).
# ---------------------------------------------------------------------------
locals {
  etl_root              = "${path.module}/../../../src/etl"
  pipeline_health_files = fileset("${local.etl_root}/pipeline_health", "*.py")
  common_files          = fileset("${local.etl_root}/common", "*.py")
}

data "archive_file" "pipeline_health_lambda_zip" {
  type        = "zip"
  output_path = "${path.module}/pipeline_health_lambda.zip"

  dynamic "source" {
    for_each = local.pipeline_health_files
    content {
      content  = file("${local.etl_root}/pipeline_health/${source.value}")
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
resource "aws_iam_role" "pipeline_health_lambda_role" {
  name = "${var.environment}-pipeline-health-lambda-role"
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
    Name        = "${var.environment}-pipeline-health-lambda-role"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}

# CloudWatch Logs write access — this Lambda's OWN log group only.
resource "aws_iam_role_policy" "pipeline_health_lambda_logging" {
  name = "${var.environment}-pipeline-health-logging"
  role = aws_iam_role.pipeline_health_lambda_role.id

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
        Resource = "arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/${var.environment}-pipeline-health:*"
      }
    ]
  })
}

# CloudWatch Logs Insights access — read-only, scoped to the 3 monitored
# pipeline Lambdas' log groups where AWS supports resource-level scoping.
resource "aws_iam_role_policy" "pipeline_health_lambda_logs_insights" {
  name = "${var.environment}-pipeline-health-logs-insights"
  role = aws_iam_role.pipeline_health_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "LogsInsightsQueryPipelineLambdas"
        Effect = "Allow"
        Action = [
          "logs:StartQuery",
          "logs:GetQueryResults",
          "logs:StopQuery",
        ]
        # Scoped to the 3 pipeline Lambdas' log groups in this environment
        # only (bronze/silver/gold) — this observer never queries any other
        # log group.
        Resource = [
          for name in var.pipeline_function_names
          : "arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/${name}:*"
        ]
      },
      {
        Sid    = "DescribeLogGroups"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogGroups",
        ]
        # AWS limitation: logs:DescribeLogGroups does not support
        # resource-level (ARN) scoping — it is a "list"-style action that
        # only accepts Resource = "*". This is documented AWS behaviour,
        # not a design gap (see AWS IAM docs for CloudWatch Logs actions).
        Resource = "*"
      }
    ]
  })
}

# CloudWatch metrics (API quota check) + Cost Explorer (cost check) —
# both API actions are Resource="*"-only by AWS design.
resource "aws_iam_role_policy" "pipeline_health_lambda_metrics_and_cost" {
  name = "${var.environment}-pipeline-health-metrics-and-cost"
  role = aws_iam_role.pipeline_health_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "GetMetricData"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricData",
        ]
        # AWS limitation: CloudWatch does not support resource-level
        # scoping for GetMetricData — no ARNs exist for CloudWatch metric
        # data, so Resource="*" is the only valid value. Not a design gap.
        Resource = "*"
      },
      {
        Sid    = "GetCostAndUsage"
        Effect = "Allow"
        Action = [
          "ce:GetCostAndUsage",
        ]
        # AWS limitation: Cost Explorer actions are Resource="*"-only by
        # AWS design — no ARNs exist for cost/usage data. Not a design gap.
        Resource = "*"
      }
    ]
  })
}

# S3 access — write gold/pipeline_health/* only (least privilege).
resource "aws_iam_role_policy" "pipeline_health_lambda_s3" {
  name = "${var.environment}-pipeline-health-s3"
  role = aws_iam_role.pipeline_health_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "WriteGoldPipelineHealth"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:PutObjectAcl"]
        # Write only within the gold/pipeline_health/ prefix — cannot
        # touch bronze, silver, or gold/aggregations.
        Resource = "${var.s3_bucket_arn}/gold/pipeline_health/*"
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Lambda function
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "pipeline_health" {
  filename         = data.archive_file.pipeline_health_lambda_zip.output_path
  function_name    = "${var.environment}-pipeline-health"
  role             = aws_iam_role.pipeline_health_lambda_role.arn
  handler          = "pipeline_health_lambda.lambda_handler"
  source_code_hash = data.archive_file.pipeline_health_lambda_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 300 # 5 minutes — bounded Logs Insights polling, small payload
  memory_size      = 256

  environment {
    variables = {
      S3_BUCKET               = var.s3_bucket_name
      PIPELINE_FUNCTION_NAMES = join(",", var.pipeline_function_names)
    }
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name        = "${var.environment}-pipeline-health"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}

# ---------------------------------------------------------------------------
# CloudWatch Log Group (30-day retention, consistent with other Lambdas)
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "pipeline_health_lambda_logs" {
  name              = "/aws/lambda/${aws_lambda_function.pipeline_health.function_name}"
  retention_in_days = 30

  tags = {
    Name        = "${var.environment}-pipeline-health-logs"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}

# ---------------------------------------------------------------------------
# EventBridge — independent weekly schedule, NOT part of the Step Functions
# pipeline_orchestrator state machine (this Lambda observes the pipeline,
# it does not participate in it). Runs 15 minutes after gold's 12:45 run
# so it can report on that week's just-completed pipeline execution.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "pipeline_health_weekly_trigger" {
  count = var.create_schedule ? 1 : 0

  name                = "${var.environment}-pipeline-health-weekly"
  description         = "Trigger pipeline health Lambda weekly, 15 min after the gold aggregator"
  schedule_expression = "cron(0 13 ? * SUN *)"

  tags = {
    Name        = "${var.environment}-pipeline-health-weekly"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}

resource "aws_cloudwatch_event_target" "pipeline_health_lambda_target" {
  count = var.create_schedule ? 1 : 0

  rule      = aws_cloudwatch_event_rule.pipeline_health_weekly_trigger[0].name
  target_id = "PipelineHealthLambda"
  arn       = aws_lambda_function.pipeline_health.arn
}

resource "aws_lambda_permission" "allow_eventbridge_pipeline_health" {
  count = var.create_schedule ? 1 : 0

  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pipeline_health.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.pipeline_health_weekly_trigger[0].arn
}
