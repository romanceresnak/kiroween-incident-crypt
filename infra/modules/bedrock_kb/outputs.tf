output "knowledge_base_id" {
  description = "Amazon Bedrock Knowledge Base ID."
  value       = aws_bedrockagent_knowledge_base.postmortems.id
}

output "knowledge_base_arn" {
  description = "Amazon Bedrock Knowledge Base ARN."
  value       = aws_bedrockagent_knowledge_base.postmortems.arn
}

output "postmortem_bucket_name" {
  description = "S3 bucket holding the postmortem source documents."
  value       = aws_s3_bucket.postmortems.id
}

output "vector_bucket_arn" {
  description = "ARN of the S3 Vectors bucket."
  value       = aws_s3vectors_vector_bucket.kb.vector_bucket_arn
}
