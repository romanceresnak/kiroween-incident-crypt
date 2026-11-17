variable "project" {
  description = "Project name prefix."
  type        = string
}

variable "env" {
  description = "Environment suffix."
  type        = string
}

variable "aws_region" {
  description = "Region of the EU inference profile ARN."
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name for the Pod Identity associations."
  type        = string
}

variable "cluster_arn" {
  description = "EKS cluster ARN. Trust policies only accept sessions tagged with this cluster."
  type        = string
}

variable "agent_names" {
  description = "Agents that each get an IAM role and a Pod Identity association."
  type        = list(string)
}

variable "namespace" {
  description = "Kubernetes namespace the agent service accounts live in."
  type        = string
  default     = "agents"
}

variable "knowledge_base_arn" {
  description = "Bedrock Knowledge Base ARN that retrieval agents may query."
  type        = string
}

variable "knowledge_base_agents" {
  description = "Agents that additionally get bedrock:Retrieve on the Knowledge Base."
  type        = list(string)
  default     = ["incident-memory"]
}
