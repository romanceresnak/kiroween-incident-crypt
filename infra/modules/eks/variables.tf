variable "project" {
  description = "Project name prefix."
  type        = string
}

variable "env" {
  description = "Environment suffix."
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name."
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes version for the EKS cluster."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for the EKS cluster and node group."
  type        = list(string)
}

variable "node_instance_types" {
  description = "Spot instance type preference list for the node group."
  type        = list(string)
}

variable "node_min_size" {
  description = "Node group minimum size."
  type        = number
}

variable "node_desired_size" {
  description = "Node group desired size."
  type        = number
}

variable "node_max_size" {
  description = "Node group maximum size."
  type        = number
}

variable "cloudwatch_log_retention_days" {
  description = "Retention period in days for the EKS control plane CloudWatch log group."
  type        = number
}

# Access entry variables — wired in TASK-07; defaulted to "" here so TASK-05
# plan passes without the github_oidc module.
variable "admin_iam_principal_arn" {
  description = "IAM principal ARN to grant AmazonEKSClusterAdminPolicy."
  type        = string
}

variable "gha_plan_role_arn" {
  description = "GitHub Actions plan role ARN to grant AmazonEKSAdminViewPolicy. Set in TASK-07."
  type        = string
  default     = ""
}

variable "gha_apply_role_arn" {
  description = "GitHub Actions apply role ARN to grant AmazonEKSClusterAdminPolicy. Set in TASK-07."
  type        = string
  default     = ""
}
