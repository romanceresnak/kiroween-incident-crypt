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
  #   terraform init -backend-config=bucket=<project>-<env>-tf-state-<account_id>
  # (the exact name is the state_bucket_name output of infra/bootstrap)
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
      Project   = var.project
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
  cluster_name      = "${var.project}-${var.env}"
  state_bucket_name = "${var.project}-${var.env}-tf-state-${data.aws_caller_identity.current.account_id}"
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

  project      = var.project
  env          = var.env
  cluster_name = local.cluster_name

  kubernetes_version = var.eks_kubernetes_version
  private_subnet_ids = module.vpc.private_subnet_ids

  node_instance_types = var.eks_node_instance_types
  node_min_size       = var.eks_node_min_size
  node_desired_size   = var.eks_node_desired_size
  node_max_size       = var.eks_node_max_size

  cloudwatch_log_retention_days = var.cloudwatch_log_retention_days

  admin_iam_principal_arn = var.admin_iam_principal_arn
  gha_plan_role_arn       = module.github_oidc.plan_role_arn
  gha_apply_role_arn      = module.github_oidc.apply_role_arn
}

# TASK-06: GitHub Actions OIDC roles
module "github_oidc" {
  source = "./modules/github_oidc"

  project    = var.project
  env        = var.env
  aws_region = var.aws_region

  gha_repo             = var.gha_repo
  state_bucket_name    = local.state_bucket_name
  create_oidc_provider = var.create_github_oidc_provider
}

# TASK-08: Bedrock Knowledge Base
module "bedrock_kb" {
  source = "./modules/bedrock_kb"

  project    = var.project
  env        = var.env
  aws_region = var.aws_region

  embedding_model_id = var.bedrock_embedding_model_id
  vector_dimension   = var.bedrock_vector_dimension
}

# TASK-09: Pod Identity IAM roles
module "pod_identity" {
  source = "./modules/pod_identity"

  project    = var.project
  env        = var.env
  aws_region = var.aws_region

  cluster_name = module.eks.cluster_name
  cluster_arn  = module.eks.cluster_arn
  agent_names  = var.agent_names

  knowledge_base_arn = module.bedrock_kb.knowledge_base_arn
}

# TASK-10: Argo CD (bootstrap only; everything else is deployed from /gitops)
module "argocd" {
  source = "./modules/argocd"

  argocd_chart_version      = var.argocd_chart_version
  argocd_apps_chart_version = var.argocd_apps_chart_version

  gitops_repo_url        = var.gitops_repo_url
  gitops_repo_path       = var.gitops_repo_path
  gitops_target_revision = var.gitops_target_revision

  # The helm provider needs a live cluster. Provider blocks cannot carry
  # depends_on, so the dependency lives on the module call.
  depends_on = [module.eks]
}

# TASK-11: FinOps (Budget)
module "finops" {
  source = "./modules/finops"

  project            = var.project
  env                = var.env
  budget_alert_email = var.budget_alert_email
}
