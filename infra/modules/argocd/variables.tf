variable "namespace" {
  description = "Namespace Argo CD and the root Application are installed into."
  type        = string
  default     = "argocd"
}

variable "argocd_chart_version" {
  description = "argo-cd Helm chart version."
  type        = string
}

variable "argocd_apps_chart_version" {
  description = "argocd-apps Helm chart version."
  type        = string
}

variable "gitops_repo_url" {
  description = "Git repository the root Application syncs from."
  type        = string
}

variable "gitops_repo_path" {
  description = "Path in the repository the root Application syncs."
  type        = string
}

variable "gitops_target_revision" {
  description = "Branch, tag or commit the root Application tracks."
  type        = string
}
