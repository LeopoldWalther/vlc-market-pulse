terraform {

  # Require Terraform >=1.2 and <2.0 to avoid accidental incompatible major upgrades.
  required_version = ">= 1.2, < 2.0"
  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Allow any 5.x provider but prevent automatic upgrade to 6.x
      version = ">= 5.0, < 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# CRITICAL: ACM certificates used with CloudFront MUST be provisioned in
# us-east-1. A certificate created in any other region is silently rejected
# by CloudFront. The alias is used for every ACM + validation resource here.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}
