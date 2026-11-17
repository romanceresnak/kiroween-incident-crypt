# Outputs are wired as module calls are added (TASK-12).
# Stubs are commented out until the source modules exist.

# output "eks_cluster_name" {
#   description = "EKS cluster name."
#   value       = module.eks.cluster_name
# }

# output "eks_cluster_endpoint" {
#   description = "EKS cluster API server endpoint."
#   value       = module.eks.cluster_endpoint
# }

# output "ecr_repository_urls" {
#   description = "Map of agent name to ECR repository URL."
#   value       = module.ecr.repository_urls
# }

# output "knowledge_base_id" {
#   description = "Amazon Bedrock Knowledge Base ID."
#   value       = module.bedrock_kb.knowledge_base_id
# }

# output "postmortem_bucket_name" {
#   description = "S3 bucket name for postmortem documents."
#   value       = module.bedrock_kb.postmortem_bucket_name
# }

# output "gha_plan_role_arn" {
#   description = "IAM role ARN for GitHub Actions terraform plan."
#   value       = module.github_oidc.plan_role_arn
# }

# output "gha_apply_role_arn" {
#   description = "IAM role ARN for GitHub Actions terraform apply and ECR push."
#   value       = module.github_oidc.apply_role_arn
# }
