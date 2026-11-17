variable "project" {
  description = "Project name prefix."
  type        = string
}

variable "env" {
  description = "Environment suffix."
  type        = string
}

variable "aws_region" {
  description = "AWS region, used to scope resource ARNs in the apply role policy."
  type        = string
}

variable "subject_prefix" {
  description = "Immutable OIDC subject prefix, repo:<owner>@<owner_id>/<repo>@<repo_id>."
  type        = string

  # GitHub issues immutable subjects for this repository (owner and repo carry their
  # numeric IDs), so a name-only prefix would never match the token.
  validation {
    condition     = can(regex("^repo:[^/@]+@[0-9]+/[^/@]+@[0-9]+$", var.subject_prefix))
    error_message = "Use the immutable form repo:<owner>@<owner_id>/<repo>@<repo_id>. Get it with: gh api repos/<owner>/<repo>/actions/oidc/customization/sub (sub_claim_prefix)."
  }
}

variable "state_bucket_name" {
  description = "Name of the Terraform state bucket the plan role reads and locks."
  type        = string
}

variable "create_oidc_provider" {
  description = "Create the GitHub OIDC provider. Set false (default) when the account already has one: only one provider per URL can exist."
  type        = bool
  default     = false
}
