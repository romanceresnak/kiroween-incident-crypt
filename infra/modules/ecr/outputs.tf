output "repository_urls" {
  description = "Map of agent name to ECR repository URL."
  value       = { for k, v in aws_ecr_repository.agent : k => v.repository_url }
}
