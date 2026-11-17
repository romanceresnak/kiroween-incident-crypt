terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.27.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.38"
    }
  }

  # Partial backend configuration.
  # Bucket is supplied at init time:
  #   terraform init -backend-config=bucket=kiroween-prod-tf-state-255834079310
  backend "s3" {
    key          = "kiroween/terraform.tfstate"
    region       = "eu-west-1"
    use_lockfile = true
    # no dynamodb_table — native S3 locking only
  }
}

# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "kiroween"
      ManagedBy = "terraform"
    }
  }
}

# Helm v3 uses attribute syntax for the kubernetes block.
# The exec block fetches a short-lived token at apply time — no stored credentials.
# The argocd module call in this file carries depends_on = [module.eks] so that
# Helm only connects after the cluster is ACTIVE. Provider blocks themselves do
# not support depends_on.
provider "helm" {
  kubernetes = {
    host                   = try(module.eks.cluster_endpoint, "")
    cluster_ca_certificate = try(base64decode(module.eks.cluster_ca_cert), "")
    exec = {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args = [
        "eks", "get-token",
        "--cluster-name", try(module.eks.cluster_name, ""),
        "--region", var.aws_region,
      ]
    }
  }
}

# Kubernetes v2 uses block syntax for exec.
provider "kubernetes" {
  host                   = try(module.eks.cluster_endpoint, "")
  cluster_ca_certificate = try(base64decode(module.eks.cluster_ca_cert), "")
  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args = [
      "eks", "get-token",
      "--cluster-name", try(module.eks.cluster_name, ""),
      "--region", var.aws_region,
    ]
  }
}

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Module calls — added task by task
# ---------------------------------------------------------------------------

# TASK-03: VPC
# module "vpc" { ... }

# TASK-04: ECR
# module "ecr" { ... }

# TASK-05 + TASK-07: EKS (cluster + access entries)
# module "eks" { ... }

# TASK-06: GitHub Actions OIDC roles
# module "github_oidc" { ... }

# TASK-08: Bedrock Knowledge Base
# module "bedrock_kb" { ... }

# TASK-09: Pod Identity IAM roles
# module "pod_identity" { ... }

# TASK-10: Argo CD
# module "argocd" { ... }

# TASK-11: FinOps (Budget)
# module "finops" { ... }
