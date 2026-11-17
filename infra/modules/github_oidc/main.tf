data "aws_caller_identity" "current" {}

locals {
  prefix        = "${var.project}-${var.env}"
  account_id    = data.aws_caller_identity.current.account_id
  oidc_host     = "token.actions.githubusercontent.com"
  state_bucket  = "arn:aws:s3:::${var.state_bucket_name}"
  plan_subject  = "repo:${var.gha_repo}:pull_request"
  apply_subject = "repo:${var.gha_repo}:ref:refs/heads/main"

  oidc_provider_arn = (
    var.create_oidc_provider
    ? aws_iam_openid_connect_provider.github[0].arn
    : data.aws_iam_openid_connect_provider.github[0].arn
  )
}

# ---------------------------------------------------------------------------
# OIDC provider (REQ-GHA-01)
# AWS validates GitHub's certificate against its trusted CA library, so no
# thumbprint_list is needed.
# ---------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1

  url = "https://${local.oidc_host}"
}

# ---------------------------------------------------------------------------
# Trust policies: exact subject match, no wildcards (REQ-GHA-07)
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "plan_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = [local.plan_subject]
    }
  }
}

data "aws_iam_policy_document" "apply_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = [local.apply_subject]
    }
  }
}

# ---------------------------------------------------------------------------
# Plan role: read-only (REQ-GHA-02, REQ-GHA-03)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "plan" {
  name               = "${local.prefix}-gha-plan"
  assume_role_policy = data.aws_iam_policy_document.plan_trust.json

  tags = { Name = "${local.prefix}-gha-plan" }
}

data "aws_iam_policy_document" "plan" {
  # Read access needed for terraform plan to refresh state.
  statement {
    sid    = "ReadInfrastructure"
    effect = "Allow"
    actions = [
      "ec2:Describe*",
      "eks:Describe*",
      "eks:List*",
      "iam:Get*",
      "iam:List*",
      "ecr:DescribeRepositories",
      "ecr:GetLifecyclePolicy",
      "ecr:GetRepositoryPolicy",
      "ecr:ListTagsForResource",
      "bedrock:Get*",
      "bedrock:List*",
      "s3vectors:GetVectorBucket",
      "s3vectors:GetVectorBucketPolicy",
      "s3vectors:GetIndex",
      "s3vectors:ListVectorBuckets",
      "s3vectors:ListIndexes",
      "s3vectors:ListTagsForResource",
      "logs:DescribeLogGroups",
      "logs:ListTagsForResource",
      "budgets:ViewBudget",
      # Bucket configuration only; object reads are limited to the state bucket below.
      "s3:ListAllMyBuckets",
      "s3:GetBucket*",
      "s3:Get*Configuration",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "StateRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${local.state_bucket}/*"]
  }

  statement {
    sid       = "StateList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket]
  }

  # use_lockfile writes and removes <key>.tflock next to the state object.
  statement {
    sid       = "StateLockfile"
    effect    = "Allow"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.state_bucket}/*.tflock"]
  }
}

resource "aws_iam_role_policy" "plan" {
  name   = "plan"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan.json
}

# ---------------------------------------------------------------------------
# Apply role: plan/apply plus ECR push (REQ-GHA-05, REQ-GHA-06)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "apply" {
  name               = "${local.prefix}-gha-apply"
  assume_role_policy = data.aws_iam_policy_document.apply_trust.json

  tags = { Name = "${local.prefix}-gha-apply" }
}

data "aws_iam_policy_document" "apply" {
  # Everything the stack manages. ec2, eks, bedrock, budgets and logs are
  # service-wide for a hackathon account; iam, s3, ecr and s3vectors are scoped
  # to this project's name prefix below.
  statement {
    sid    = "ManageServices"
    effect = "Allow"
    actions = [
      "ec2:*",
      "eks:*",
      "bedrock:*",
      "budgets:*",
      "logs:*",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "ManageProjectBuckets"
    effect  = "Allow"
    actions = ["s3:*"]
    resources = [
      # The trailing * also matches object keys, so one ARN covers buckets and objects.
      "arn:aws:s3:::${local.prefix}-*",
    ]
  }

  statement {
    sid       = "ListBuckets"
    effect    = "Allow"
    actions   = ["s3:ListAllMyBuckets"]
    resources = ["*"]
  }

  statement {
    sid       = "ManageProjectVectors"
    effect    = "Allow"
    actions   = ["s3vectors:*"]
    resources = ["arn:aws:s3vectors:${var.aws_region}:${local.account_id}:bucket/${local.prefix}-*"]
  }

  statement {
    sid       = "ListVectorBuckets"
    effect    = "Allow"
    actions   = ["s3vectors:ListVectorBuckets"]
    resources = ["*"]
  }

  # ECR repository management and image push, limited to <project>/* repos.
  statement {
    sid    = "EcrRepositoriesAndPush"
    effect = "Allow"
    actions = [
      "ecr:CreateRepository",
      "ecr:DeleteRepository",
      "ecr:DescribeRepositories",
      "ecr:PutImageScanningConfiguration",
      "ecr:PutImageTagMutability",
      "ecr:PutLifecyclePolicy",
      "ecr:GetLifecyclePolicy",
      "ecr:DeleteLifecyclePolicy",
      "ecr:GetRepositoryPolicy",
      "ecr:ListTagsForResource",
      "ecr:TagResource",
      "ecr:UntagResource",
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchDeleteImage",
      "ecr:ListImages",
    ]
    resources = ["arn:aws:ecr:${var.aws_region}:${local.account_id}:repository/${var.project}/*"]
  }

  statement {
    sid       = "EcrLogin"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # IAM is limited to roles and policies carrying the project prefix.
  statement {
    sid     = "ManageProjectIam"
    effect  = "Allow"
    actions = ["iam:*"]
    resources = [
      "arn:aws:iam::${local.account_id}:role/${local.prefix}-*",
      "arn:aws:iam::${local.account_id}:policy/${local.prefix}-*",
      "arn:aws:iam::${local.account_id}:oidc-provider/${local.oidc_host}",
    ]
  }

  statement {
    sid       = "ReadIam"
    effect    = "Allow"
    actions   = ["iam:Get*", "iam:List*"]
    resources = ["*"]
  }

  statement {
    sid       = "ServiceLinkedRoles"
    effect    = "Allow"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values = [
        "eks.amazonaws.com",
        "eks-nodegroup.amazonaws.com",
      ]
    }
  }
}

resource "aws_iam_role_policy" "apply" {
  name   = "apply"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.apply.json
}
