output "cluster_name" {
  description = "EKS cluster name."
  value       = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  description = "EKS cluster API server endpoint."
  value       = aws_eks_cluster.main.endpoint
}

output "cluster_ca_cert" {
  description = "Base64-encoded cluster CA certificate."
  value       = aws_eks_cluster.main.certificate_authority[0].data
}

output "cluster_arn" {
  description = "EKS cluster ARN."
  value       = aws_eks_cluster.main.arn
}

output "oidc_issuer_url" {
  description = "OIDC issuer URL for the EKS cluster (used for IRSA if needed)."
  value       = aws_eks_cluster.main.identity[0].oidc[0].issuer
}

output "cluster_security_group_id" {
  description = "EKS-created cluster security group. Managed node group nodes, and so pod IPs, use it."
  value       = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
}
