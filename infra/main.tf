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
# The argocd module call below carries depends_on = [module.eks] so Helm only
# connects after the cluster is ACTIVE. Provider blocks do not support depends_on.
provider "helm" {
  kubernetes = {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_ca_cert)
    exec = {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args = [
        "eks", "get-token",
        "--cluster-name", module.eks.cluster_name,
        "--region", var.aws_region,
      ]
    }
  }
}

# Kubernetes v2 uses block syntax for exec.
provider "kubernetes" {
  host                   = module.eks.cluster_endpoint
  cluster_ca_certificate = base64decode(module.eks.cluster_ca_cert)
  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args = [
      "eks", "get-token",
      "--cluster-name", module.eks.cluster_name,
      "--region", var.aws_region,
    ]
  }
}

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------

locals {
  cluster_name = "${var.project}-${var.env}"
}

# ---------------------------------------------------------------------------
# Module calls
# ---------------------------------------------------------------------------

# TASK-03: VPC
module "vpc" {
  source = "./modules/vpc"

  project      = var.project
  env          = var.env
  aws_region   = var.aws_region
  cluster_name = local.cluster_name
}

# TASK-04: ECR
module "ecr" {
  source = "./modules/ecr"

  project     = var.project
  agent_names = var.agent_names
}

# TASK-05 + TASK-07: EKS (cluster, node group, add-ons, access entries)
module "eks" {
  source = "./modules/eks"

  project    = var.project
  env        = var.env
  cluster_name = local.cluster_name

  kubernetes_version = var.eks_kubernetes_version
  private_subnet_ids = module.vpc.private_subnet_ids

  node_instance_types = var.eks_node_instance_types
  node_min_size       = var.eks_node_min_size
  node_desired_size   = var.eks_node_desired_size
  node_max_size       = var.eks_node_max_size

  cloudwatch_log_retention_days = var.cloudwatch_log_retention_days

  admin_iam_principal_arn = var.admin_iam_principal_arn
  # gha_plan_role_arn and gha_apply_role_arn wired in TASK-07
}

# TASK-06: GitHub Actions OIDC roles
# module "github_oidc" { ... }

# TASK-08: Bedrock Knowledge Base
# module "bedrock_kb" { ... }

# TASK-09: Pod Identity IAM roles
# module "pod_identity" { ... }

# TASK-10: Argo CD
# module "argocd" {
#   ...
#   depends_on = [module.eks]
# }

# TASK-11: FinOps (Budget)
# module "finops" { ... }
