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

# Numeric GitHub IDs used by immutable OIDC subject claims. They never change
# when the owner or repository is renamed, which is why GitHub embeds them.
# Read them with: gh api repos/<owner>/<repo>/actions/oidc/customization/sub
variable "github_owner_id" {
  type        = string
  description = "Numeric GitHub ID of the repository owner (immutable OIDC subjects)."
  default     = "32717417"
}

variable "github_repo_id" {
  type        = string
  description = "Numeric GitHub ID of the repository (immutable OIDC subjects)."
  default     = "1123272680"
}
