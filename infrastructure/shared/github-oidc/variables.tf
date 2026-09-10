variable "github_org" {
  type        = string
  description = "GitHub organisation or user that owns the repository."
  default     = "LeopoldWalther"
}

variable "github_repo" {
  type        = string
  description = "GitHub repository name (without the org prefix)."
  default     = "vlc-market-pulse"
}
