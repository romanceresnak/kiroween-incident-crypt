variable "project" {
  description = "Project name prefix for all resource names."
  type        = string
  default     = "kiroween"
}

variable "env" {
  description = "Environment suffix (e.g. prod, dev)."
  type        = string
  default     = "prod"
}

variable "aws_region" {
  description = "AWS region for the state bucket."
  type        = string
  default     = "eu-west-1"
}
