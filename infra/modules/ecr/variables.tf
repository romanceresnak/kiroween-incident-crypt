variable "project" {
  description = "Project name prefix, used as ECR repository namespace."
  type        = string
}

variable "agent_names" {
  description = "List of agent names. One ECR repository is created per agent."
  type        = list(string)
}
