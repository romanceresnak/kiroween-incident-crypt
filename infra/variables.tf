variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "eu-west-1"
}

variable "project" {
  description = "Project name prefix used in all resource names."
  type        = string
  default     = "kiroween"
}

variable "env" {
  description = "Environment suffix (e.g. prod, dev)."
  type        = string
  default     = "prod"
}

variable "eks_kubernetes_version" {
  description = "Kubernetes version for the EKS cluster. Latest standard-support version as of Nov 2026."
  type        = string
  default     = "1.37"
}

variable "eks_node_instance_types" {
  description = "Spot instance type preference list for the EKS node group."
  type        = list(string)
  default     = ["t3.large", "t3a.large", "m5.large"]
}

variable "eks_node_min_size" {
  description = "EKS node group minimum size."
  type        = number
  default     = 2
}

variable "eks_node_desired_size" {
  description = "EKS node group desired size."
  type        = number
  default     = 2
}

variable "eks_node_max_size" {
  description = "EKS node group maximum size."
  type        = number
  default     = 3
}

variable "admin_iam_principal_arn" {
  description = "IAM principal ARN to grant AmazonEKSClusterAdminPolicy via EKS access entry."
  type        = string
}

variable "argocd_chart_version" {
  description = "argo-cd Helm chart version to pin."
  type        = string
  default     = "10.18.0"
}

variable "argocd_apps_chart_version" {
  description = "argocd-apps Helm chart version to pin."
  type        = string
  default     = "2.0.8"
}

variable "gitops_repo_url" {
  description = "URL of the GitOps repository for the Argo CD app-of-apps Application."
  type        = string
  default     = "https://github.com/romanceresnak/kiroween-incident-crypt"
}

variable "gitops_repo_path" {
  description = "Path within the GitOps repository for the Argo CD app-of-apps Application."
  type        = string
  default     = "gitops"
}

variable "gitops_target_revision" {
  description = "Branch or tag for the Argo CD app-of-apps Application."
  type        = string
  default     = "main"
}

variable "agent_names" {
  description = "List of agent names. Drives ECR repository and Pod Identity role for_each."
  type        = list(string)
  default     = ["orchestrator", "cpu-memory", "network", "deploy-rollout", "storage", "incident-memory"]
}

variable "bedrock_embedding_model_id" {
  description = "Bedrock model ID for Knowledge Base vector embeddings."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "bedrock_vector_dimension" {
  description = "Vector dimension for the S3 Vectors index. Must match the embedding model output."
  type        = number
  default     = 1024
}

variable "gha_repo" {
  description = "GitHub repository path (org/repo) used to scope OIDC subject conditions."
  type        = string
  default     = "romanceresnak/kiroween-incident-crypt"
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC provider. Leave false when the account already has one (only one per URL is allowed); set true in a fresh account."
  type        = bool
  default     = false
}

variable "budget_alert_email" {
  description = "Email address for the AWS Budget $50/month alert notification."
  type        = string
  sensitive   = true
}

variable "cloudwatch_log_retention_days" {
  description = "Retention period in days for all CloudWatch log groups created by this stack."
  type        = number
  default     = 7
}
