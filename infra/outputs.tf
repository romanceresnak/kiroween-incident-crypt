output "eks_cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  description = "EKS cluster API server endpoint."
  value       = module.eks.cluster_endpoint
}

output "ecr_repository_urls" {
  description = "Map of agent name to ECR repository URL."
  value       = module.ecr.repository_urls
}

output "knowledge_base_id" {
  description = "Amazon Bedrock Knowledge Base ID."
  value       = module.bedrock_kb.knowledge_base_id
}

output "postmortem_bucket_name" {
  description = "S3 bucket name for postmortem documents."
  value       = module.bedrock_kb.postmortem_bucket_name
}

output "gha_plan_role_arn" {
  description = "IAM role ARN for GitHub Actions terraform plan."
  value       = module.github_oidc.plan_role_arn
}

output "gha_apply_role_arn" {
  description = "IAM role ARN for GitHub Actions terraform apply and ECR push."
  value       = module.github_oidc.apply_role_arn
}

output "gha_ecr_push_role_arn" {
  description = "IAM role ARN for GitHub Actions ECR image push."
  value       = module.github_oidc.ecr_push_role_arn
}

output "public_url" {
  description = "HTTPS address of the login-protected system, for judges."
  value       = module.public_access.public_url
}

output "target_group_arn" {
  description = "Target group ARN for the orchestrator TargetGroupBinding (chart value publicAccess.targetGroupArn)."
  value       = module.public_access.target_group_arn
}

output "vpc_id" {
  description = "VPC ID, for the AWS Load Balancer Controller Application."
  value       = module.vpc.vpc_id
}

output "private_subnet_cidrs" {
  description = "Private subnet CIDRs: the source addresses of the internal ALB (chart value publicAccess.albCidrs)."
  value       = module.vpc.private_subnet_cidrs
}

output "lb_controller_role_arn" {
  description = "IAM role of the AWS Load Balancer Controller."
  value       = module.lb_controller.role_arn
}
