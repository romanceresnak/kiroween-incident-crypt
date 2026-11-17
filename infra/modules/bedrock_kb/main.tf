data "aws_caller_identity" "current" {}

locals {
  prefix         = "${var.project}-${var.env}"
  account_id     = data.aws_caller_identity.current.account_id
  embedding_arn  = "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.embedding_model_id}"
  postmortem_arn = aws_s3_bucket.postmortems.arn
}

# ---------------------------------------------------------------------------
# Postmortem source bucket (REQ-KB-01)
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "postmortems" {
  bucket = "${local.prefix}-postmortems-${local.account_id}"

  tags = { Name = "${local.prefix}-postmortems" }
}

resource "aws_s3_bucket_versioning" "postmortems" {
  bucket = aws_s3_bucket.postmortems.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "postmortems" {
  bucket = aws_s3_bucket.postmortems.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "postmortems" {
  bucket = aws_s3_bucket.postmortems.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# S3 Vectors store (REQ-KB-02)
# ---------------------------------------------------------------------------

resource "aws_s3vectors_vector_bucket" "kb" {
  vector_bucket_name = "${local.prefix}-vectors"

  tags = { Name = "${local.prefix}-vectors" }
}

resource "aws_s3vectors_index" "kb" {
  vector_bucket_name = aws_s3vectors_vector_bucket.kb.vector_bucket_name
  index_name         = "bedrock-kb-index"

  data_type       = "float32"
  dimension       = var.vector_dimension
  distance_metric = "cosine"

  # Bedrock stores the chunk text and its metadata under these keys. They must
  # be non-filterable or they count against the 2 KB filterable-metadata limit.
  metadata_configuration {
    non_filterable_metadata_keys = ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]
  }

  tags = { Name = "bedrock-kb-index" }
}

# ---------------------------------------------------------------------------
# Knowledge Base service role (REQ-KB-05): exactly the actions Bedrock needs
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "kb_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock:${var.aws_region}:${local.account_id}:knowledge-base/*"]
    }
  }
}

resource "aws_iam_role" "kb" {
  name               = "${local.prefix}-kb"
  assume_role_policy = data.aws_iam_policy_document.kb_trust.json

  tags = { Name = "${local.prefix}-kb" }
}

data "aws_iam_policy_document" "kb" {
  statement {
    sid    = "VectorIndex"
    effect = "Allow"
    actions = [
      "s3vectors:PutVectors",
      "s3vectors:QueryVectors",
      "s3vectors:GetVectors",
      "s3vectors:DeleteVectors",
      "s3vectors:GetIndex",
    ]
    resources = [aws_s3vectors_index.kb.index_arn]
  }

  statement {
    sid       = "ReadSourceObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${local.postmortem_arn}/*"]
  }

  statement {
    sid       = "ListSourceBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [local.postmortem_arn]
  }

  statement {
    sid       = "InvokeEmbeddingModel"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel"]
    resources = [local.embedding_arn]
  }
}

resource "aws_iam_role_policy" "kb" {
  name   = "knowledge-base"
  role   = aws_iam_role.kb.id
  policy = data.aws_iam_policy_document.kb.json
}

# ---------------------------------------------------------------------------
# Knowledge Base and data source (REQ-KB-03, REQ-KB-04)
# ---------------------------------------------------------------------------

resource "aws_bedrockagent_knowledge_base" "postmortems" {
  name     = "${local.prefix}-postmortems"
  role_arn = aws_iam_role.kb.arn

  knowledge_base_configuration {
    type = "VECTOR"

    vector_knowledge_base_configuration {
      embedding_model_arn = local.embedding_arn

      embedding_model_configuration {
        bedrock_embedding_model_configuration {
          dimensions          = var.vector_dimension
          embedding_data_type = "FLOAT32"
        }
      }
    }
  }

  storage_configuration {
    type = "S3_VECTORS"

    s3_vectors_configuration {
      index_arn = aws_s3vectors_index.kb.index_arn
    }
  }

  tags = { Name = "${local.prefix}-postmortems" }

  # The role's policy must exist before Bedrock validates access to the index.
  depends_on = [aws_iam_role_policy.kb]
}

resource "aws_bedrockagent_data_source" "postmortems" {
  name              = "${local.prefix}-postmortems-s3"
  knowledge_base_id = aws_bedrockagent_knowledge_base.postmortems.id

  data_source_configuration {
    type = "S3"

    s3_configuration {
      bucket_arn = aws_s3_bucket.postmortems.arn
    }
  }
}
