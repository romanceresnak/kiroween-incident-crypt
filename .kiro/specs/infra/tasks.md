# Infra Spec — Tasks

Tasks are ordered so each one builds on the previous. Every task is independently
testable before moving to the next. Requirement IDs in brackets map to
`requirements.md`.

---

## TASK-01 — Bootstrap module

**Implements:** REQ-BS-01, REQ-BS-02, REQ-BS-03, REQ-RS-01, REQ-RS-03, REQ-RS-04

Create `infra/bootstrap/` with:
- `variables.tf` — `project`, `env`, `aws_region` (default `eu-west-1`)
- `main.tf` — `aws_s3_bucket` with a name following the
  `<project>-<env>-tf-state-<account_id>` convention; enable versioning
  (`aws_s3_bucket_versioning`); enable SSE-S3 (`aws_s3_bucket_server_side_encryption_configuration`);
  block all public access (`aws_s3_bucket_public_access_block`)
- `outputs.tf` — `state_bucket_name`

**Test:** `terraform init && terraform validate` passes inside `infra/bootstrap/`.
Apply produces the bucket; `aws s3api get-bucket-versioning` and
`get-bucket-encryption` confirm the configuration.

---

## TASK-02 — Root backend + provider skeleton

**Implements:** REQ-RS-02, REQ-RS-04, REQ-GEN-01, REQ-FIN-01

Create `infra/main.tf`, `infra/variables.tf`, `infra/outputs.tf`:
- `terraform {}` block with `required_version = ">= 1.9.0"`, all three
  `required_providers` (aws `>= 6.27.0`, helm `>= 2.13.0`, kubernetes `>= 2.30.0`),
  and a partial `backend "s3"` block containing `key`, `region`, and
  `use_lockfile = true` — **no `bucket` attribute**
- AWS provider with `region = var.aws_region` and `default_tags`
  (`Project = "kiroween"`, `ManagedBy = "terraform"`)
- Helm and Kubernetes provider stubs (references to `module.eks` outputs; will
  fail until TASK-05 — use `depends_on` on the module call, not on the provider block)
- All variables from the design's variable table with their documented defaults

**Test:** `terraform init -backend-config=bucket=<bootstrap-output>` succeeds.
`terraform validate` passes (providers are configured even if modules are not yet
wired).

---

## TASK-03 — VPC module

**Implements:** REQ-VPC-01, REQ-VPC-02, REQ-VPC-03, REQ-VPC-04, REQ-VPC-05, REQ-VPC-06

Create `infra/modules/vpc/`:
- VPC with `enable_dns_hostnames = true`, `enable_dns_support = true`
- 2 public subnets + 2 private subnets across two AZs (use
  `data "aws_availability_zones"` to pick the first two available)
- Internet Gateway attached to the VPC
- One NAT gateway in the first public subnet (single-AZ)
- Public route table: `0.0.0.0/0 → IGW`; private route table: `0.0.0.0/0 → NAT`
- Subnet tags: `kubernetes.io/role/elb = 1` on public, `kubernetes.io/role/internal-elb = 1`
  on private; `kubernetes.io/cluster/<cluster_name> = shared` on all four

**Test:** Wire the module in root `main.tf`, run `terraform plan`. Confirm 4
subnets, 1 NAT gateway, 2 route tables. After apply, `aws ec2 describe-vpcs`
and `describe-subnets` show the expected topology.

---

## TASK-04 — ECR repositories

**Implements:** REQ-ECR-01, REQ-ECR-02, REQ-ECR-03, REQ-ECR-04

Create `infra/modules/ecr/`:
- `for_each = toset(var.agent_names)` over one `aws_ecr_repository` per agent
- `image_scanning_configuration { scan_on_push = true }` on each
- One `aws_ecr_lifecycle_policy` per repository retaining the 10 most-recent
  tagged images and expiring all untagged images after 1 day
- Output: `repository_urls` map

**Test:** `terraform plan` shows 6 repositories + 6 lifecycle policies.
After apply, `aws ecr describe-repositories` lists all six.

---

## TASK-05 — EKS cluster, node group, and add-ons

**Implements:** REQ-EKS-01, REQ-EKS-02, REQ-EKS-05, REQ-EKS-06, REQ-EKS-07, REQ-EKS-08, REQ-EKS-09

Create `infra/modules/eks/`:
- `aws_eks_cluster` with `version = var.eks_kubernetes_version` (`"1.37"`),
  `access_config { authentication_mode = "API" }`, CloudWatch logging for
  `api`, `audit`, `authenticator`
- `aws_cloudwatch_log_group` for the cluster with `retention_in_days = var.cloudwatch_log_retention_days`
- `aws_eks_node_group` with `instance_types`, `capacity_type = "SPOT"`,
  `scaling_config { min_size, desired_size, max_size }`, and
  `update_config { max_unavailable = 1 }`
- `aws_eks_addon` resources: `eks-pod-identity-agent`, `aws-ebs-csi-driver`
  (resolve latest addon versions from the EKS API)
- Outputs: `cluster_name`, `cluster_endpoint`, `cluster_ca_cert`, `cluster_arn`,
  `oidc_issuer_url`

**Test:** After apply, `aws eks describe-cluster` shows status `ACTIVE`, auth
mode `API`, and both add-ons. Node group reaches `ACTIVE` with 2 Spot nodes.

---

## TASK-06 — EKS access entries

**Implements:** REQ-EKS-02, REQ-EKS-03, REQ-EKS-04, REQ-GHA-04

Add to `infra/modules/eks/` (or a dedicated sub-resource in the same module):
- `aws_eks_access_entry` + `aws_eks_access_policy_association` for
  `var.admin_iam_principal_arn` → `AmazonEKSClusterAdminPolicy` (cluster scope)
- `aws_eks_access_entry` + `aws_eks_access_policy_association` for
  `var.gha_apply_role_arn` → `AmazonEKSClusterAdminPolicy` (cluster scope)
- `aws_eks_access_entry` + `aws_eks_access_policy_association` for
  `var.gha_plan_role_arn` → `AmazonEKSAdminViewPolicy` (cluster scope)

The apply and plan role ARNs are outputs of the `github_oidc` module; pass them
in as variables. Use `depends_on` on these access entry resources to ensure the
cluster is `ACTIVE` before attempting association.

**Test:** `aws eks list-access-entries --cluster-name <name>` shows all three
principals. `aws eks describe-access-entry` for each confirms the correct
associated policy.

---

## TASK-07 — GitHub Actions OIDC roles

**Implements:** REQ-GHA-01, REQ-GHA-02, REQ-GHA-03, REQ-GHA-05, REQ-GHA-06, REQ-GHA-07

Create `infra/modules/github_oidc/`:
- `aws_iam_openid_connect_provider` for `token.actions.githubusercontent.com`
  with the correct thumbprint list
- **Plan role** (`aws_iam_role`):
  - Trust: OIDC provider, subject `repo:<var.gha_repo>:pull_request`
  - Policy: read-only on EC2, EKS, IAM, S3, ECR, Bedrock, S3 Vectors; plus
    `s3:GetObject` on the state bucket; `s3:PutObject` and `s3:DeleteObject`
    scoped to keys matching `*.tflock` on the state bucket only
- **Apply role** (`aws_iam_role`):
  - Trust: OIDC provider, subject `repo:<var.gha_repo>:ref:refs/heads/main`
  - Policy: full plan/apply permissions across all managed service namespaces
    plus ECR push actions
- Outputs: `plan_role_arn`, `apply_role_arn`

**Test:** `terraform plan` shows two roles with no wildcard `*` subjects.
Verify trust policy documents with `aws iam get-role`.

---

## TASK-08 — Pod Identity IAM roles

**Implements:** REQ-IAM-01, REQ-IAM-02, REQ-IAM-03, REQ-IAM-04, REQ-IAM-05

Create `infra/modules/pod_identity/`:
- `for_each = toset(var.agent_names)` over `aws_iam_role` resources
- Trust policy on each: `pods.eks.amazonaws.com`, `sts:AssumeRole` +
  `sts:TagSession`, condition scoped to `var.cluster_arn` and the agent's
  service account name (`<agent>-sa` in namespace `agents`)
- Inline policy on each role:
  - `bedrock:InvokeModel` + `bedrock:InvokeModelWithResponseStream` on:
    - `arn:aws:bedrock:eu-west-1:<account_id>:inference-profile/eu.*`
    - `arn:aws:bedrock:eu-*::foundation-model/*`
  - For `incident-memory` only: additionally `bedrock:Retrieve` on
    `var.knowledge_base_arn`
- `aws_eks_pod_identity_association` per agent binding role → service account
  `<agent>-sa` in namespace `agents`
- Output: `agent_role_arns` map

**Test:** `terraform plan` shows 6 roles + 6 associations. Verify trust
documents: each has the correct cluster ARN condition and no write-capable
actions.

---

## TASK-09 — S3 postmortem bucket + S3 Vectors + Bedrock Knowledge Base

**Implements:** REQ-KB-01, REQ-KB-02, REQ-KB-03, REQ-KB-04, REQ-KB-05, REQ-KB-06

Create `infra/modules/bedrock_kb/`:
- `aws_s3_bucket` for postmortems: versioning enabled, public access blocked,
  server-side encryption
- `aws_s3vectors_bucket` (requires provider `>= 6.27.0`)
- `aws_s3vectors_index` with `dimension = 1024`, `distance_metric = "cosine"`,
  `non_filterable_metadata_keys = ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]`
- IAM role for the Knowledge Base service (`aws_iam_role` with
  `bedrock.amazonaws.com` as principal) + inline policy with exactly the
  actions from REQ-KB-05 (`s3vectors:PutVectors`, `s3vectors:QueryVectors`,
  `s3vectors:GetVectors`, `s3vectors:DeleteVectors`, `s3vectors:GetIndex`,
  `s3:GetObject`, `s3:ListBucket`, `bedrock:InvokeModel` on Titan V2)
- `aws_bedrockagent_knowledge_base` with `storage_configuration.type = "S3_VECTORS"`,
  `embedding_model_arn` pointing at `amazon.titan-embed-text-v2:0`
- `aws_bedrockagent_data_source` wiring the postmortem bucket as the S3 data source
- Outputs: `knowledge_base_id`, `knowledge_base_arn`, `postmortem_bucket_name`,
  `vector_bucket_arn`

**Test:** `terraform plan` succeeds with no unknown values on the KB resource.
After apply, `aws bedrock-agent get-knowledge-base` shows status `ACTIVE` and
storage type `S3_VECTORS`.

---

## TASK-10 — Argo CD install + app-of-apps

**Implements:** REQ-ACD-01, REQ-ACD-02, REQ-ACD-03, REQ-ACD-04, REQ-ACD-05

Create `infra/modules/argocd/`:
- `helm_release` for `argo-cd` chart version `10.16.0` from
  `https://argoproj.github.io/argo-helm`, namespace `argocd`,
  `create_namespace = true`; no values override for the admin secret
  (use Argo CD's default `argocd-initial-admin-secret`)
- `helm_release` for `argocd-apps` chart version `2.0.8`, same namespace,
  `depends_on = [helm_release.argocd]`; values configure one `Application`
  pointing at `var.gitops_repo_url` / `var.gitops_repo_path` /
  `var.gitops_target_revision = "main"` with `automated { prune = true, selfHeal = true }`

**Test:** After apply, `kubectl -n argocd get applications` shows the
`app-of-apps` Application in `Synced` / `Healthy` state. `helm list -n argocd`
shows both releases.

---

## TASK-11 — AWS Budget

**Implements:** REQ-FIN-02

Create `infra/modules/finops/`:
- `aws_budgets_budget` with `budget_type = "COST"`, `time_unit = "MONTHLY"`,
  `limit_amount = "50"`, `limit_unit = "USD"`
- One `notification` block: `threshold = 100`, `threshold_type = "PERCENTAGE"`,
  `notification_type = "ACTUAL"`, subscriber email = `var.budget_alert_email`

**Test:** `terraform plan` shows the budget resource. After apply,
`aws budgets describe-budgets` lists the budget with the correct threshold.

---

## TASK-12 — Wire root module + outputs

**Implements:** REQ-GEN-01, REQ-GEN-02, REQ-GEN-03

Wire all modules in root `main.tf`, passing outputs between modules:
- `module.vpc` → `module.eks` (subnet IDs)
- `module.eks` → `module.argocd` (cluster outputs; `depends_on`)
- `module.eks` → `module.pod_identity` (cluster ARN)
- `module.bedrock_kb` → `module.pod_identity` (knowledge_base_arn)
- `module.github_oidc` → `module.eks` (plan + apply role ARNs for access entries)
- `module.github_oidc` → `module.pod_identity` (state bucket name for plan role policy)

Populate root `outputs.tf` with all seven outputs from the design:
`eks_cluster_name`, `eks_cluster_endpoint`, `ecr_repository_urls`,
`knowledge_base_id`, `postmortem_bucket_name`, `gha_plan_role_arn`,
`gha_apply_role_arn`.

Confirm no hardcoded account IDs, regions, or bucket names appear anywhere
outside `variables.tf` defaults.

**Test:** `terraform validate` passes with zero errors or warnings.

---

## TASK-13 — `terraform.tfvars.example` and README

**Implements:** REQ-GEN-01

Create `infra/terraform.tfvars.example`:

```hcl
# Required — no defaults
admin_iam_principal_arn = "arn:aws:iam::123456789012:role/my-admin-role"
budget_alert_email      = "ops@example.com"

# Optional overrides (defaults shown)
# aws_region                   = "eu-west-1"
# project                      = "kiroween"
# env                          = "prod"
# eks_kubernetes_version        = "1.37"
# argocd_chart_version          = "10.16.0"
# argocd_apps_chart_version     = "2.0.8"
# cloudwatch_log_retention_days = 7
```

Add a brief `infra/README.md` documenting: (1) run bootstrap once, (2) init
main stack with the bucket name, (3) supply required variables via `TF_VAR_*`
in CI.

**Test:** A developer following the README can reach `terraform plan` without
any "Required variable not set" errors.

---

## TASK-14 — `terraform validate` + plan smoke-test

**Implements:** REQ-GEN-01, REQ-GEN-02 (validation gate)

This task is the acceptance check for the full configuration:

1. Run `terraform validate` in `infra/` — must exit 0 with no errors.
2. Run `terraform plan -out=tfplan` against a real AWS account with the
   required variables set:
   - Confirm the plan contains no `Error` lines.
   - Confirm all 7 root outputs are present in the plan output.
   - Confirm no resource uses a hardcoded account ID or region string.
3. Run `terraform show -json tfplan | jq '.variables'` and verify
   `eks_kubernetes_version = "1.37"`, `project = "kiroween"`,
   `cloudwatch_log_retention_days = 7`.

**Test:** Steps 1–3 pass cleanly. This task is complete only when `validate`
and `plan` both succeed without errors.
