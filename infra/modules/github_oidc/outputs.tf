output "plan_role_arn" {
  description = "IAM role ARN for GitHub Actions terraform plan (pull_request)."
  value       = aws_iam_role.plan.arn
}

output "apply_role_arn" {
  description = "IAM role ARN for GitHub Actions terraform apply and ECR push (main branch)."
  value       = aws_iam_role.apply.arn
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub OIDC provider the roles trust."
  value       = local.oidc_provider_arn
}
