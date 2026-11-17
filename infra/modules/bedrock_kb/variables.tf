variable "project" {
  description = "Project name prefix."
  type        = string
}

variable "env" {
  description = "Environment suffix."
  type        = string
}

variable "aws_region" {
  description = "AWS region. Used to build the embedding model ARN."
  type        = string
}

variable "embedding_model_id" {
  description = "Bedrock embedding model ID."
  type        = string
}

variable "vector_dimension" {
  description = "Vector dimension. Must be a dimension the embedding model supports (Titan Text Embeddings V2: 256, 512 or 1024)."
  type        = number
}
