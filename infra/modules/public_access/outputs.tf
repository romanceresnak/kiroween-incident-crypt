output "cloudfront_domain" {
  description = "The *.cloudfront.net domain of the distribution."
  value       = aws_cloudfront_distribution.this.domain_name
}

output "public_url" {
  description = "HTTPS address judges use."
  value       = "https://${aws_cloudfront_distribution.this.domain_name}"
}

output "target_group_arn" {
  description = "Target group the orchestrator pods register into (TargetGroupBinding)."
  value       = aws_lb_target_group.orchestrator.arn
}

output "alb_security_group_id" {
  description = "Security group of the internal ALB."
  value       = aws_security_group.alb.id
}

output "web_acl_arn" {
  description = "WAF web ACL on the distribution."
  value       = aws_wafv2_web_acl.this.arn
}
