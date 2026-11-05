terraform {
  required_version = ">= 1.7, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 6.0"
    }
  }
}

# The secret VALUES are not managed by Terraform. Keeping them out of the
# configuration keeps plaintext credentials out of the state file, so CI can
# plan and apply without ever seeing them. Rotate values with
# `aws secretsmanager put-secret-value` (see README, "Rotating Idealista
# credentials"). Terraform only owns the secret container and its metadata.
resource "aws_secretsmanager_secret" "idealista_credentials_lvw" {
  name        = "${var.environment}/idealista/lvw-api-credentials"
  description = "Idealista API credentials for leopold.walther@gmail.com in ${var.environment} environment"

  recovery_window_in_days = 7

  tags = {
    Name        = "${var.environment}-idealista-lvw-credentials"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}

resource "aws_secretsmanager_secret" "idealista_credentials_pmv" {
  name        = "${var.environment}/idealista/pmv-api-credentials"
  description = "Idealista API credentials for paulamarinvillar@gmail.com in ${var.environment} environment"

  recovery_window_in_days = 7

  tags = {
    Name        = "${var.environment}-idealista-pmv-credentials"
    Environment = var.environment
    ManagedBy   = "terraform"
    Project     = "valencia-real-estate"
  }
}
