output "namespace" {
  description = "Namespace Argo CD is installed in."
  value       = helm_release.argocd.namespace
}

output "argocd_chart_version" {
  description = "Deployed argo-cd chart version."
  value       = helm_release.argocd.version
}
