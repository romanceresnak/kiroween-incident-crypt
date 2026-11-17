variable "project" {
  description = "Project name prefix."
  type        = string
}

variable "env" {
  description = "Environment suffix."
  type        = string
}

variable "budget_alert_email" {
  description = "Email address that receives the budget alert."
  type        = string
  sensitive   = true
}

variable "limit_usd" {
  description = "Monthly cost limit in USD."
  type        = number
}
