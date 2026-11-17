output "state_bucket_name" {
  description = "Name of the S3 bucket used for Terraform remote state. Pass to: terraform init -backend-config=bucket=<value>"
  value       = aws_s3_bucket.state.id
}
