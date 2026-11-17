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

variable "gha_repo" {
  description = "GitHub repository (org/repo) allowed to assume the roles."
  type        = string
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
