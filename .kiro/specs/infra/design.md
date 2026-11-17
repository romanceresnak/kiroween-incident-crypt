# Infra Spec — Design

## Module layout

```
infra/
├── bootstrap/              # Local-state only. Run once before the main stack.
│   ├── main.tf             # aws_s3_bucket (state bucket), optional aws_kms_key
│   ├── variables.tf
│   └── outputs.tf          # state_bucket_name
│
├── .gitignore              # *.tfstate*, *.tfstate.*.backup, .terraform/
├── main.tf                 # Root: provider config, backend block, module calls
├── variables.tf
├── outputs.tf
│
└── modules/
    ├── vpc/                # VPC, subnets, IGW, single NAT, route tables
    │   ├── main.tf
    │   ├── variables.tf
    │   └── outputs.tf      # vpc_id, private_subnet_ids, public_subnet_ids
    │
    ├── eks/                # Cluster, node group, add-ons, access entries, CW log group
    │   ├── main.tf
    │   ├── variables.tf
    │   └── outputs.tf      # cluster_name, cluster_endpoint, cluster_ca_cert,
    │                       # cluster_arn, oidc_issuer_url
    │
    ├── argocd/             # Two helm_releases: argo-cd + argocd-apps (app-of-apps)
    │   ├── main.tf
    │   ├── variables.tf
    │   └── outputs.tf
    │
    ├── ecr/                # for_each over agent_names
    │   ├── main.tf
    │   ├── variables.tf
    │   └── outputs.tf      # repository_urls = map(agent_name → url)
    │
    ├── bedrock_kb/         # S3 postmortem bucket, S3 Vectors bucket+index, KB, data source
    │   ├── main.tf
    │   ├── variables.tf
    │   └── outputs.tf      # knowledge_base_id, knowledge_base_arn,
    │                       # postmortem_bucket_name, vector_bucket_arn
    │
    ├── pod_identity/       # for_each over agent_names → IAM role + association
    │   ├── main.tf
    │   ├── variables.tf
    │   └── outputs.tf      # agent_role_arns = map(agent_name → arn)
    │
    ├── github_oidc/        # OIDC provider, plan role, apply role, ECR push role
    │   ├── main.tf
    │   ├── variables.tf
    │   └── outputs.tf      # plan_role_arn, apply_role_arn, ecr_push_role_arn
    │
    └── finops/             # AWS Budget, (CW retention set inline in each module)
        ├── main.tf
        ├── variables.tf
        └── outputs.tf
```

CloudWatch log retention is set directly on each `aws_cloudwatch_log_group` resource inside the module that creates it (e.g. `eks/`), driven by a shared `var.cloudwatch_log_retention_days` passed in from the root. `finops/` owns only the AWS Budget.

### `.gitignore` (fix #6)

`infra/.gitignore` must contain at minimum:

```
*.tfstate
*.tfstate.*
.terraform/
.terraform.lock.hcl
terraform.tfvars
```

The bootstrap local state file (`infra/bootstrap/terraform.tfstate`) must never be committed. It contains the state bucket ARN, which is not a secret, but committing state files creates merge conflicts and false diffs. Store the bootstrap state file outside the repo (e.g. a secure S3 bucket, a local directory not under version control, or 1Password) and document the location in the repo README.

---

## Provider configuration

### Version constraints

```hcl
terraform {
  required_version = ">= 1.9.0"   # native S3 locking (use_lockfile) requires >= 1.9

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.27.0"        # minimum for aws_s3vectors_vector_bucket / aws_s3vectors_index
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

  # Partial backend configuration — bucket is supplied at init time only.
  # terraform init -backend-config=bucket=<name-from-bootstrap-output>
  backend "s3" {
    key          = "kiroween/terraform.tfstate"
    region       = "eu-west-1"
    use_lockfile = true
    # no bucket attribute here — partial configuration (fix #3)
    # no dynamodb_table — native S3 locking only
  }
}
```

### AWS provider

```hcl
provider "aws" {
  region = var.aws_region   # default "eu-west-1"

  default_tags {
    tags = {
      Project   = "kiroween"
      ManagedBy = "terraform"
    }
  }
}
```

`default_tags` propagates to every taggable resource automatically. Per-resource `tags` blocks are reserved for resource-specific labels (e.g. EKS subnet discovery tags); they merge with `default_tags`.

### Helm and Kubernetes providers

Both providers derive credentials from the EKS cluster after it exists. They use an `exec` block so tokens are fetched at apply time and never stored in state:

```hcl
provider "helm" {
  kubernetes = {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_ca_cert)
    exec = {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", module.eks.cluster_name,
                     "--region", var.aws_region]
    }
  }
}

provider "kubernetes" {
  host                   = module.eks.cluster_endpoint
  cluster_ca_certificate = base64decode(module.eks.cluster_ca_cert)
  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", module.eks.cluster_name,
                   "--region", var.aws_region]
  }
}
```

The `argocd` module call in `main.tf` uses `depends_on = [module.eks]` to ensure the cluster exists before Helm connects. Provider blocks themselves do not support `depends_on`.

---

## Naming conventions

All names follow `<project>-<env>-<component>[-<qualifier>]` using `var.project` (default `"kiroween"`) and `var.env`. This avoids collisions across environments and makes ownership obvious from the AWS console.

| Resource | Name pattern | Example |
|---|---|---|
| VPC | `<project>-<env>-vpc` | `kiroween-prod-vpc` |
| EKS cluster | `<project>-<env>` | `kiroween-prod` |
| Node group | `<project>-<env>-spot` | `kiroween-prod-spot` |
| ECR repository | `<project>/<agent>` | `kiroween/orchestrator` |
| S3 state bucket | `<project>-<env>-tf-state-<account_id>` | (account suffix ensures global uniqueness) |
| S3 postmortem bucket | `<project>-<env>-postmortems-<account_id>` | |
| S3 Vectors bucket | `<project>-<env>-vectors` | `kiroween-prod-vectors` |
| S3 Vectors index | `bedrock-kb-index` | (single index per bucket) |
| Bedrock Knowledge Base | `<project>-<env>-postmortems` | `kiroween-prod-postmortems` |
| Pod Identity role | `<project>-<env>-<agent>` | `kiroween-prod-orchestrator` |
| GHA plan role | `<project>-<env>-gha-plan` | `kiroween-prod-gha-plan` |
| GHA apply role | `<project>-<env>-gha-apply` | `kiroween-prod-gha-apply` |
| GHA ECR push role | `<project>-<env>-gha-ecr-push` | `kiroween-prod-gha-ecr-push` |
| AWS Budget | `<project>-<env>-monthly` | `kiroween-prod-monthly` |
| CloudWatch log group | `/aws/eks/<cluster_name>/cluster` | (EKS-managed pattern) |

ECR uses `<project>/<agent>` (slash-separated) so all agent images share a logical registry namespace.

---

## Variables

### Root `variables.tf`

| Variable | Type | Default | Required | Purpose |
|---|---|---|---|---|
| `aws_region` | `string` | `"eu-west-1"` | | Deployment region |
| `project` | `string` | `"kiroween"` | | Name prefix |
| `env` | `string` | `"prod"` | | Environment suffix |
| `eks_kubernetes_version` | `string` | `"1.37"` | | K8s version — latest standard-support in EKS as of Nov 2026 (REQ-EKS-01) |
| `eks_node_instance_types` | `list(string)` | `["t3.large","t3a.large","m5.large"]` | | Spot preference list (REQ-EKS-05) |
| `eks_node_min_size` | `number` | `2` | | Node group min (REQ-EKS-05) |
| `eks_node_desired_size` | `number` | `2` | | Node group desired (REQ-EKS-05) |
| `eks_node_max_size` | `number` | `3` | | Node group max (REQ-EKS-05) |
| `admin_iam_principal_arn` | `string` | — | ✓ | Cluster-admin access entry IAM principal (REQ-EKS-03) |
| `argocd_chart_version` | `string` | `"10.18.0"` | | argo-cd chart version — latest as of 17 Nov 2026 (helm repo index) (REQ-ACD-02) |
| `argocd_apps_chart_version` | `string` | `"2.0.8"` | | argocd-apps chart version — latest as of 17 Nov 2026 (helm repo index) (REQ-ACD-03) |
| `gitops_repo_url` | `string` | `"https://github.com/romanceresnak/kiroween-incident-crypt"` | | App-of-apps source repo (REQ-ACD-03) |
| `gitops_repo_path` | `string` | `"gitops/apps"` | | App-of-apps source path: holds only Argo CD `Application` manifests, the children of the root app (REQ-ACD-03) |
| `gitops_target_revision` | `string` | `"main"` | | App-of-apps branch (REQ-ACD-03) |
| `agent_names` | `list(string)` | `["orchestrator","cpu-memory","network","deploy-rollout","storage","incident-memory"]` | | Drives ECR + Pod Identity `for_each` (REQ-ECR-01, REQ-IAM-01) |
| `bedrock_embedding_model_id` | `string` | `"amazon.titan-embed-text-v2:0"` | | KB embedding model (REQ-KB-03) |
| `bedrock_vector_dimension` | `number` | `1024` | | S3 Vectors index dimension (REQ-KB-02) |
| `gha_repo` | `string` | `"romanceresnak/kiroween-incident-crypt"` | | GitHub repo for OIDC subject scoping (REQ-GHA-02, REQ-GHA-05) |
| `budget_limit_usd` | `number` | `120` | | Monthly AWS Budget limit in USD; must be > 0 (REQ-FIN-02) |
| `budget_alert_email` | `string` | — | ✓ | Recipient for the budget alert (REQ-FIN-02) |
| `cloudwatch_log_retention_days` | `number` | `7` | | Retention for all CW log groups (REQ-EKS-08, REQ-FIN-03) |

Variables marked Required have no default and must be supplied via `TF_VAR_*` environment variables in CI. They are never stored in a committed `terraform.tfvars` file. A `terraform.tfvars.example` documents every variable.

---

## Outputs

Defined in root `outputs.tf`, sourced from child module outputs:

| Output | Source | Consumed by |
|---|---|---|
| `eks_cluster_name` | `module.eks.cluster_name` | GHA `kubectl`/Helm steps, agent config |
| `eks_cluster_endpoint` | `module.eks.cluster_endpoint` | Helm/kubernetes providers |
| `ecr_repository_urls` | `module.ecr.repository_urls` | GHA image build/push steps |
| `knowledge_base_id` | `module.bedrock_kb.knowledge_base_id` | Agent environment config |
| `postmortem_bucket_name` | `module.bedrock_kb.postmortem_bucket_name` | Postmortem upload scripts |
| `gha_plan_role_arn` | `module.github_oidc.plan_role_arn` | GHA workflow `role-to-assume` |
| `gha_apply_role_arn` | `module.github_oidc.apply_role_arn` | GHA Terraform workflow `role-to-assume` |
| `gha_ecr_push_role_arn` | `module.github_oidc.ecr_push_role_arn` | GHA image workflow `role-to-assume` (REQ-GHA-08) |

All outputs carry a `description`. None are marked `sensitive` — ARNs and URLs are operational metadata, not secrets. GHA workflows capture them with `terraform output -json` and pass them as step outputs scoped to the jobs that need them.

---

## Key design decisions

### Bootstrap isolation (REQ-BS-01, REQ-BS-02)
`infra/bootstrap/` uses local state and creates only the S3 state bucket (and optionally a KMS key). It has no dependency on the main stack. After the first `apply`, the operator runs `terraform init -backend-config=bucket=<name>` in `infra/` to initialise the main stack's remote backend using partial backend configuration (the `bucket` attribute is omitted from the `backend "s3"` block in source; it is always supplied at init time). The bootstrap state file must never be committed to git — see the `.gitignore` section above.

### Single root, no workspaces
Multiple environments are handled by distinct backend keys and `var.env` values, not Terraform workspaces. Workspaces share a single backend configuration and silently apply to whichever workspace is selected — a footgun in CI. Separate backend keys make environment isolation explicit and auditable.

### `for_each` over `agent_names` (REQ-ECR-04, REQ-IAM-01)
All per-agent resources (ECR repository, Pod Identity IAM role, Pod Identity association) are created with `for_each = toset(var.agent_names)`. Adding an agent requires only updating the variable default; no new resource blocks are needed.

### Argo CD app-of-apps via `argocd-apps` chart (REQ-ACD-01, REQ-ACD-02, REQ-ACD-03)
The root Argo CD `Application` is deployed as a second `helm_release` using the argo-helm `argocd-apps` chart (`2.0.8`), with `depends_on` pointing at the first `helm_release` (argo-cd `10.18.0`). This avoids the `kubernetes_manifest` approach, which requires the Argo CD CRDs to already be registered at plan time and fails on a fresh cluster with `Error: no matches for kind "Application"`. The `argocd-apps` chart is purpose-built for this pattern and renders `Application` CRs as Helm templates after the CRDs exist.

The root Application syncs `gitops/apps` (`var.gitops_repo_path`), not the whole `gitops/` tree. That directory holds only child `Application` manifests; workloads such as `gitops/charts/agents/` are referenced from those children. Pointing the root app at `gitops/` would make it apply chart sources and raw manifests directly and lose the one-Application-per-workload boundary.

```hcl
# modules/argocd/main.tf (sketch)

resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = var.argocd_chart_version          # "10.18.0"
  namespace        = "argocd"
  create_namespace = true
}

resource "helm_release" "argocd_apps" {
  name       = "argocd-apps"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = var.argocd_apps_chart_version           # "2.0.8"
  namespace  = "argocd"

  values = [yamlencode({
    # argocd-apps 2.x: `applications` is a map keyed by Application name.
    applications = {
      "app-of-apps" = {
        namespace = "argocd"
        project   = "default"
        source = {
          repoURL        = var.gitops_repo_url
          targetRevision = var.gitops_target_revision
          path           = var.gitops_repo_path
        }
        destination = {
          server    = "https://kubernetes.default.svc"
          namespace = "argocd"
        }
        syncPolicy = {
          automated = { prune = true, selfHeal = true }
        }
      }
    }
  })]

  depends_on = [helm_release.argocd]
}
```

### Titan V2 is in-region only (REQ-KB-03, REQ-IAM-03)
Amazon Titan Text Embeddings V2 does not have a Geo/EU cross-region inference profile — the model card shows `Geo: not supported`. It is invoked directly in `eu-west-1` using `amazon.titan-embed-text-v2:0`. The EU cross-region profile prefix `eu.*` applies only to agent LLMs. Pod Identity IAM policies therefore allow `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` on:
- The inference profile ARN: `arn:aws:bedrock:eu-west-1:<account_id>:inference-profile/eu.*`
- The underlying foundation-model ARNs across all EU destination regions: `arn:aws:bedrock:eu-*::foundation-model/*`

The Knowledge Base service role is separate and allows only `bedrock:InvokeModel` on the Titan V2 ARN in `eu-west-1` (REQ-KB-05).

### `AmazonEKSAdminViewPolicy` for the plan role (REQ-GHA-04)
The Terraform `helm` and `kubernetes` providers read existing release state from the Kubernetes API during `plan`. `AmazonEKSAdminViewPolicy` grants cluster-scoped `get/list/watch` on all resource types — the minimum that covers every API group those providers query. `AmazonEKSViewPolicy` is namespace-scoped and would require one access entry association per namespace, adding operational overhead for no security benefit in this context.

### No `terraform.tfvars` committed to git (REQ-GEN-03)
Sensitive and environment-specific values (`admin_iam_principal_arn`, `budget_alert_email`) are passed as `TF_VAR_*` environment variables in CI. A `terraform.tfvars.example` documents every variable with a placeholder value and a comment.

### Separate ECR push role (REQ-GHA-06, REQ-GHA-08)
Image builds run on every change to agent code, so the job that pushes images should not hold Terraform's permissions. The ECR push role has the same trust subject as the apply role (a push to `main`) but can only log in to ECR and push to `kiroween/*`. The apply role keeps ECR repository management, which Terraform needs, and loses push and login. Neither role can be assumed from a pull request.

### VPC CNI as a managed add-on (REQ-EKS-10)
EKS clusters start with a self-managed `aws-node` DaemonSet. In this cluster its network policy agent runs with `--enable-network-policy=false`, so `NetworkPolicy` objects do nothing. Managing `vpc-cni` as an `aws_eks_addon` with `enableNetworkPolicy = "true"` turns enforcement on. `resolve_conflicts_on_create = "OVERWRITE"` lets EKS adopt the running DaemonSet. The add-on version comes from `data "aws_eks_addon_version"` like the other add-ons. Enabling enforcement changes nothing until a `NetworkPolicy` exists, but updating the add-on rolls the `aws-node` pods.

### Immutable image tags (REQ-ECR-05)
The image workflow tags each image with the commit SHA. With immutable repositories a tag can be pushed once, so the ECR push role cannot replace an image that a running Deployment already references. The cost is that re-running a workflow for the same commit must skip the push.
