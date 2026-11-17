output "agent_role_arns" {
  description = "Map of agent name to its Pod Identity IAM role ARN."
  value       = { for k, r in aws_iam_role.agent : k => r.arn }
}

output "agent_service_accounts" {
  description = "Map of agent name to the service account name its role is bound to. The Helm chart must create service accounts with these names."
  value       = { for k, a in aws_eks_pod_identity_association.agent : k => a.service_account }
}
