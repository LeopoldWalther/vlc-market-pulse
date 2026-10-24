terraform {
  required_version = ">= 1.2, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 6.0"
    }
  }
}

# ---------------------------------------------------------------------------
# State machine definition — ASL rendered via templatefile so Lambda and SNS
# ARNs are injected at plan time; no hardcoded ARNs in the JSON file.
#
# Every environment sends exactly one email per run (success OR failure).
# dev and prod share the same inbox, so the subject leads with an
# environment icon + label to keep the two apart at a glance, followed by
# a status icon. Distinct subjects also stop Gmail from threading dev and
# prod into one conversation.
# ---------------------------------------------------------------------------
locals {
  environment_icons = {
    dev     = "🧪"
    staging = "🚧"
    prod    = "🏠"
  }
  environment_label = upper(var.environment)
  subject_prefix    = "${local.environment_icons[var.environment]} ${local.environment_label}"

  asl_definition = templatefile("${path.module}/state_machine.asl.json.tftpl", {
    bronze_function_arn = var.bronze_function_arn
    silver_function_arn = var.silver_function_arn
    gold_function_arn   = var.gold_function_arn
    sns_topic_arn       = var.sns_topic_arn
    test_mode           = var.test_mode
    environment_label   = local.environment_label
    success_subject     = "${local.subject_prefix} ✅ VLC Market Pulse - weekly pipeline succeeded"
    failure_subject     = "${local.subject_prefix} ❌ VLC Market Pulse - weekly pipeline FAILED"
  })
}

# ---------------------------------------------------------------------------
# Step Functions state machine — STANDARD type so each execution is fully
# auditable and the per-state history is visible in the console.
# ---------------------------------------------------------------------------
resource "aws_sfn_state_machine" "medallion_pipeline" {
  name       = "${var.environment}-medallion-pipeline"
  role_arn   = aws_iam_role.sfn_execution.arn
  definition = local.asl_definition

  # ALL-level logging to CloudWatch so failures can be diagnosed without
  # needing to enable X-Ray tracing.
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn_logs.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tags = {
    Name        = "${var.environment}-medallion-pipeline"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }

  depends_on = [aws_iam_role_policy.sfn_execution]
}

# ---------------------------------------------------------------------------
# CloudWatch Log Group — /aws/vendedlogs/ prefix is required for Step
# Functions; plain /aws/states/ is rejected at apply time.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "sfn_logs" {
  name              = "/aws/vendedlogs/states/${var.environment}-medallion-pipeline"
  retention_in_days = 30

  tags = {
    Name        = "${var.environment}-medallion-pipeline-logs"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}

# ---------------------------------------------------------------------------
# IAM — state machine execution role
#
# Permissions (least-privilege):
#   lambda:InvokeFunction — exactly the three pipeline Lambda ARNs
#   sns:Publish           — the existing notifications topic (failure alerts)
#   logs:*                — deliver execution history to the log group above
# ---------------------------------------------------------------------------
resource "aws_iam_role" "sfn_execution" {
  name        = "${var.environment}-medallion-pipeline-sfn-role"
  description = "Step Functions execution role for the medallion pipeline (${var.environment})."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "StepFunctionsTrust"
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name        = "${var.environment}-medallion-pipeline-sfn-role"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}

resource "aws_iam_role_policy" "sfn_execution" {
  name = "${var.environment}-medallion-pipeline-sfn-policy"
  role = aws_iam_role.sfn_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ----------------------------------------------------------------
      # Invoke the three pipeline Lambda functions.
      # Scoped to the exact three ARNs — no wildcard on function names.
      # ----------------------------------------------------------------
      {
        Sid    = "InvokePipelineLambdas"
        Effect = "Allow"
        Action = "lambda:InvokeFunction"
        Resource = [
          var.bronze_function_arn,
          var.silver_function_arn,
          var.gold_function_arn,
        ]
      },
      # ----------------------------------------------------------------
      # Publish failure notifications to the SNS topic.
      # ----------------------------------------------------------------
      {
        Sid      = "PublishFailureNotification"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = var.sns_topic_arn
      },
      # ----------------------------------------------------------------
      # Deliver execution history to CloudWatch Logs.
      # CreateLogDelivery + related actions are required by Step Functions;
      # the resource wildcard is unfortunately unavoidable here — AWS does
      # not support resource-level restriction on log delivery APIs.
      # ----------------------------------------------------------------
      {
        Sid    = "CloudWatchLogsDelivery"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups",
        ]
        Resource = "*"
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# EventBridge Scheduler — single weekly trigger: Sunday 12:00 UTC
#
# A dedicated Scheduler (not EventBridge Rules) is used so the trigger
# passes a typed, structured payload including test_mode.
# The scheduler has its own minimal IAM role (review M3 — separate role so
# the trigger cannot assume the state-machine execution role or do anything
# other than start the execution).
# ---------------------------------------------------------------------------
resource "aws_scheduler_schedule" "weekly_pipeline" {
  name        = "${var.environment}-medallion-pipeline-weekly"
  description = "Start the medallion pipeline state machine every Sunday at 12:00 UTC."

  # FLEXIBLE_WINDOW_OFF: start at exactly 12:00 UTC, no jitter.
  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 12 ? * SUN *)"
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_sfn_state_machine.medallion_pipeline.arn
    role_arn = aws_iam_role.scheduler_trigger.arn

    # Empty input — test_mode is baked into the ASL definition at plan time
    # via templatefile, so the execution input does not need to carry it.
    input = jsonencode({})
  }
}

# ---------------------------------------------------------------------------
# IAM — scheduler trigger role (minimal: states:StartExecution only)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "scheduler_trigger" {
  name        = "${var.environment}-medallion-pipeline-scheduler-role"
  description = "EventBridge Scheduler role: starts the medallion pipeline state machine only."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SchedulerTrust"
        Effect = "Allow"
        Principal = {
          Service = "scheduler.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name        = "${var.environment}-medallion-pipeline-scheduler-role"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}

resource "aws_iam_role_policy" "scheduler_trigger" {
  name = "${var.environment}-medallion-pipeline-scheduler-policy"
  role = aws_iam_role.scheduler_trigger.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "StartPipelineExecution"
        Effect   = "Allow"
        Action   = "states:StartExecution"
        Resource = aws_sfn_state_machine.medallion_pipeline.arn
      }
    ]
  })
}
