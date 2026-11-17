# Infra Spec — Tasks

Tasks are ordered so each one builds on the previous. Each task is independently
testable with `terraform plan` only — no `apply` until TASK-15, **except TASK-01**
(bootstrap applies a single S3 bucket before the main stack can be initialised).
Requirement IDs in brackets map to `requirements.md`.

> **Apply policy:** The main stack (`infra/`) is applied once, in TASK-15, after
> TASK-14 passes. The bootstrap module (`infra/bootstrap/`) is applied in TASK-01
> as a one-time prerequisite and is not subject to this restriction.

---

## TASK-01 — Bootstrap module

**Implements:** REQ-BS-01, REQ-BS-02, REQ-BS-03, REQ-RS-01, REQ-RS-03, REQ-RS-04

Create `infra/bootstrap/` with:
- `variables.tf` — `project`, `env`, `aws_region` (default `"eu-west-1"`)
- `main.tf` — `aws_s3_bucket` named `<project>-<env>-tf-state-<account_id>`;
  `aws_s3_bucket_versioning` (enabled); `aws_s3_bucket_server_side_encryption_configuration`
  (SSE-S3); `aws_s3_bucket_public_access_block` (all blocked)
- `outputs.tf` — `state_bucket_name`

The `.gitignore` at the repo root (already committed) covers bootstrap state;
no additional step needed.

**Test:** `terraform init && terraform validate` inside `infra/bootstrap/`
exits 0. Run `terraform apply` to create the bucket (bootstrap is exempt from
the apply-once policy). Confirm the bucket exists: `aws s3api head-bucket`
returns 200; `get-bucket-versioning` and `get-bucket-encryption` confirm the
configuration. Note the output `state_bucket_name` for use in TASK-02.

---

## TASK-02 — Root backend + provider skeleton

**Implements:** REQ-RS-02, REQ-RS-04, REQ-GEN-01, REQ-FIN-01

Create `infra/main.tf`, `infra/variables.tf`, `infra/outputs.tf`:
- `terraform {}` block: `required_version = ">= 1.9.0"`, providers
  (`aws >= 6.27.0`, `helm ~> 3.0`, `kubernetes ~> 2.38`), and a partial
  `backend "s3"` block with `key`, `region = "eu-west-1"`, `use_lockfile = true`
  — **no `bucket` attribute** (supplied at `terraform init` time)
- AWS provider with `region = var.aws_region` and `default_tags`
  (`Project = "kiroween"`, `ManagedBy = "terraform"`)
- Helm provider using v3 attribute syntax (`kubernetes = { ... }`,
  `exec = { ... }`); Kubernetes provider using v2 block syntax
  (`exec { ... }`, pinned to `~> 2.38`); both referencing `module.eks` outputs
  (the `argocd` module call in `main.tf` will carry `depends_on = [module.eks]`;
  provider blocks do not support `depends_on`)
- All variables from the design's variable table with their documented defaults

**Test:** `terraform init -backend-config=bucket=<bootstrap-output> && terraform validate`
exits 0.

---

## TASK-03 — VPC module

**Implements:** REQ-VPC-01, REQ-VPC-02, REQ-VPC-03, REQ-VPC-04, REQ-VPC-05, REQ-VPC-06

Create `infra/modules/vpc/`:
- VPC with `enable_dns_hostnames = true`
- 2 public + 2 private subnets across two AZs (via `data "aws_availability_zones"`)
- Internet Gateway; one NAT gateway in the first public subnet
- Public route table: `0.0.0.0/0 → IGW`; private route table: `0.0.0.0/0 → NAT`
- Subnet tags: `kubernetes.io/role/elb = 1` (public),
  `kubernetes.io/role/internal-elb = 1` (private),
  `kubernetes.io/cluster/<cluster_name> = shared` (all four)

**Test:** `terraform plan` for the root module shows 1 VPC, 4 subnets, 1 NAT
gateway, 2 route tables, correct tags — no errors.

---

## TASK-04 — ECR repositories

**Implements:** REQ-ECR-01, REQ-ECR-02, REQ-ECR-03, REQ-ECR-04

Create `infra/modules/ecr/`:
- `for_each = toset(var.agent_names)` over `aws_ecr_repository` resources
- `image_scanning_configuration { scan_on_push = true }` on each
- One `aws_ecr_lifecycle_policy` per repository: retain 10 most-recent tagged
  images; expire untagged images after 1 day
- Output: `repository_urls` map

**Test:** `terraform plan` shows exactly 6 repositories and 6 lifecycle
policies — no errors.

---

## TASK-05 — EKS cluster, node group, and add-ons

**Implements:** REQ-EKS-01, REQ-EKS-02, REQ-EKS-05, REQ-EKS-06, REQ-EKS-07,
REQ-EKS-08, REQ-EKS-09

Create `infra/modules/eks/`:
- `aws_eks_cluster` with `version = var.eks_kubernetes_version` (`"1.37"`),
  `access_config { authentication_mode = "API" }` (REQ-EKS-02), CloudWatch
  logging for `api`, `audit`, `authenticator`
- `aws_cloudwatch_log_group` with `retention_in_days = var.cloudwatch_log_retention_days`
- `aws_eks_node_group`: `instance_types`, `capacity_type = "SPOT"`,
  `scaling_config { min_size = 2, desired_size = 2, max_size = 3 }`,
  `update_config { max_unavailable = 1 }`
- `aws_eks_addon`: `eks-pod-identity-agent`, `aws-ebs-csi-driver`
- Outputs: `cluster_name`, `cluster_endpoint`, `cluster_ca_cert`, `cluster_arn`,
  `oidc_issuer_url`

**Test:** `terraform plan` shows the cluster, node group, 2 add-ons, and log
group — no errors.

---

## TASK-06 — GitHub Actions OIDC roles

**Implements:** REQ-GHA-01, REQ-GHA-02, REQ-GHA-03, REQ-GHA-05, REQ-GHA-06,
REQ-GHA-07

Create `infra/modules/github_oidc/`:
- `aws_iam_openid_connect_provider` for `token.actions.githubusercontent.com`
  with the correct thumbprint list
- **Plan role** (`aws_iam_role`):
  - Trust: subject `repo:<var.gha_repo>:pull_request`
  - Policy: read-only on EC2, EKS, IAM, S3, ECR, Bedrock, S3 Vectors; plus
    `s3:GetObject` on the state bucket; `s3:PutObject` and `s3:DeleteObject`
    scoped to keys matching `*.tflock` on the state bucket only
- **Apply role** (`aws_iam_role`):
  - Trust: subject `repo:<var.gha_repo>:ref:refs/heads/main`
  - Policy: full plan/apply permissions across all managed service namespaces
    plus ECR push actions
- Outputs: `plan_role_arn`, `apply_role_arn`

**Test:** `terraform plan` shows two IAM roles with no wildcard `*` subjects in
either trust policy — no errors.

---

## TASK-07 — EKS access entries

**Implements:** REQ-EKS-03, REQ-EKS-04, REQ-GHA-04

Add to `infra/modules/eks/` (access entry resources depend on `github_oidc`
outputs, so pass them in as variables):
- `aws_eks_access_entry` + `aws_eks_access_policy_association` for
  `var.admin_iam_principal_arn` → `AmazonEKSClusterAdminPolicy` (cluster scope)
- `aws_eks_access_entry` + `aws_eks_access_policy_association` for
  `var.gha_apply_role_arn` → `AmazonEKSClusterAdminPolicy` (cluster scope)
- `aws_eks_access_entry` + `aws_eks_access_policy_association` for
  `var.gha_plan_role_arn` → `AmazonEKSAdminViewPolicy` (cluster scope)

**Test:** `terraform plan` shows 3 access entries and 3 policy associations —
no errors.

---

## TASK-08 — S3 postmortem bucket + S3 Vectors + Bedrock Knowledge Base

**Implements:** REQ-KB-01, REQ-KB-02, REQ-KB-03, REQ-KB-04, REQ-KB-05, REQ-KB-06

Create `infra/modules/bedrock_kb/`:
- `aws_s3_bucket` for postmortems: versioning enabled, public access blocked,
  SSE
- `aws_s3vectors_bucket` (requires provider `>= 6.27.0`)
- `aws_s3vectors_index`: `dimension = 1024`, `distance_metric = "cosine"`,
  `non_filterable_metadata_keys = ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]`
- IAM service role for Bedrock KB with inline policy covering exactly the
  actions from REQ-KB-05: `s3vectors:PutVectors`, `s3vectors:QueryVectors`,
  `s3vectors:GetVectors`, `s3vectors:DeleteVectors`, `s3vectors:GetIndex`,
  `s3:GetObject`, `s3:ListBucket`, `bedrock:InvokeModel` (Titan V2 ARN only)
- `aws_bedrockagent_knowledge_base`: `storage_configuration.type = "S3_VECTORS"`,
  embedding model `amazon.titan-embed-text-v2:0`
- `aws_bedrockagent_data_source` wiring the postmortem bucket
- Outputs: `knowledge_base_id`, `knowledge_base_arn`, `postmortem_bucket_name`,
  `vector_bucket_arn`

**Test:** `terraform plan` shows the S3 bucket, vector bucket, vector index,
KB, and data source with no unknown values — no errors.

---

## TASK-09 — Pod Identity IAM roles

**Implements:** REQ-IAM-01, REQ-IAM-02, REQ-IAM-03, REQ-IAM-04, REQ-IAM-05

Create `infra/modules/pod_identity/`:
- `for_each = toset(var.agent_names)` over `aws_iam_role` resources
- Trust policy: `pods.eks.amazonaws.com` principal, `sts:AssumeRole` +
  `sts:TagSession`, condition scoped to `var.cluster_arn` and the agent's
  service account (`<agent>-sa` in namespace `agents`)
- Inline policy on each role: `bedrock:InvokeModel` +
  `bedrock:InvokeModelWithResponseStream` on
  `arn:aws:bedrock:eu-west-1:<account>:inference-profile/eu.*` and
  `arn:aws:bedrock:eu-*::foundation-model/*`
- For `incident-memory` only: additionally `bedrock:Retrieve` on
  `var.knowledge_base_arn`
- `aws_eks_pod_identity_association` per agent: role → `<agent>-sa` in
  namespace `agents`
- Output: `agent_role_arns` map

**Test:** `terraform plan` shows 6 roles and 6 Pod Identity associations. The
`incident-memory` role plan shows the extra `bedrock:Retrieve` statement — no
errors.

---

## TASK-10 — Argo CD install + app-of-apps

**Implements:** REQ-ACD-01, REQ-ACD-02, REQ-ACD-03, REQ-ACD-04, REQ-ACD-05

Create `infra/modules/argocd/`:
- `helm_release` for `argo-cd` chart `10.16.0` from
  `https://argoproj.github.io/argo-helm`, namespace `argocd`,
  `create_namespace = true`; no admin secret override (use Argo CD's default
  `argocd-initial-admin-secret`)
- `helm_release` for `argocd-apps` chart `2.0.8`, same namespace,
  `depends_on = [helm_release.argocd]`; values configure one `Application`
  pointing at `var.gitops_repo_url` / `var.gitops_repo_path` /
  `var.gitops_target_revision = "main"` with
  `automated { prune = true, selfHeal = true }`
- Module call in root `main.tf` carries `depends_on = [module.eks]`

**Test:** `terraform plan` shows both `helm_release` resources — no errors.

---

## TASK-11 — AWS Budget

**Implements:** REQ-FIN-02

Create `infra/modules/finops/`:
- `aws_budgets_budget`: `budget_type = "COST"`, `time_unit = "MONTHLY"`,
  `limit_amount = "50"`, `limit_unit = "USD"`
- Notification: `threshold = 100`, `threshold_type = "PERCENTAGE"`,
  `notification_type = "ACTUAL"`, subscriber email = `var.budget_alert_email`

**Test:** `terraform plan` shows the budget resource — no errors.

---

## TASK-12 — Wire root module + outputs

**Implements:** REQ-GEN-01, REQ-GEN-02, REQ-GEN-03

Wire all modules in root `main.tf`, passing outputs between modules in
dependency order:
- `module.vpc` → `module.eks` (subnet IDs)
- `module.github_oidc` → `module.eks` (plan + apply role ARNs for access entries)
- `module.eks` → `module.argocd` (`depends_on`)
- `module.eks` → `module.pod_identity` (cluster ARN)
- `module.bedrock_kb` → `module.pod_identity` (knowledge_base_arn)

Populate root `outputs.tf` with all seven outputs:
`eks_cluster_name`, `eks_cluster_endpoint`, `ecr_repository_urls`,
`knowledge_base_id`, `postmortem_bucket_name`, `gha_plan_role_arn`,
`gha_apply_role_arn`.

Verify no hardcoded account IDs, regions, or bucket names appear outside
`variables.tf` defaults.

**Test:** `terraform plan` shows all seven outputs in the plan — no errors.

---

## TASK-13 — `terraform.tfvars.example` and README

**Implements:** REQ-GEN-01

Create `infra/terraform.tfvars.example`:

```hcl
# Required — no defaults
admin_iam_principal_arn = "arn:aws:iam::123456789012:role/my-admin-role"
budget_alert_email      = "ops@example.com"

# Optional overrides (defaults shown)
# aws_region                    = "eu-west-1"
# project                       = "kiroween"
# env                           = "prod"
# eks_kubernetes_version         = "1.37"
# argocd_chart_version           = "10.16.0"
# argocd_apps_chart_version      = "2.0.8"
# cloudwatch_log_retention_days  = 7
```

Create `infra/README.md` documenting: (1) run bootstrap once; (2) init the
main stack with the bucket name; (3) supply required variables via `TF_VAR_*`
in CI.

**Test:** A developer following the README can reach `terraform validate`
without any "Required variable not set" errors.

---

## TASK-14 — `terraform validate` + plan smoke-test

**Implements:** REQ-GEN-01, REQ-GEN-02 (validation gate)

1. Run `terraform validate` in `infra/` — must exit 0 with no errors or
   warnings.
2. Run `terraform plan -out=tfplan` with all required variables set:
   - Confirm the plan contains no `Error` lines.
   - Confirm all 7 root outputs appear in the plan output.
   - Confirm no resource block contains a hardcoded account ID or region string.
3. Run `terraform show -json tfplan | jq '.variables'` and verify
   `eks_kubernetes_version = "1.37"`, `project = "kiroween"`,
   `cloudwatch_log_retention_days = 7`.

**Test:** Steps 1–3 all pass cleanly. This task is complete only when both
`validate` and `plan` succeed without errors.

---

## TASK-15 — Full `terraform apply`

**Implements:** All requirements (acceptance)

With TASK-14 green, apply in two phases to handle the Helm and Kubernetes
providers requiring a live cluster endpoint:

**Phase 1 — cluster only:**
```
terraform apply -target=module.vpc -target=module.eks -target=module.github_oidc
```
Wait until EKS cluster status is `ACTIVE` and node group is `ACTIVE`. The Helm
and Kubernetes providers can now resolve their `exec`-based credentials.

**Phase 2 — full apply:**
```
terraform apply
```

Verify post-apply:
- EKS cluster `ACTIVE`, node group shows 2 Spot nodes.
- Both Argo CD Helm releases `deployed`; `kubectl -n argocd get applications`
  shows the `app-of-apps` Application in `Synced` / `Healthy` state.
- `aws bedrock-agent get-knowledge-base` shows status `ACTIVE` and storage
  type `S3_VECTORS`.
- `aws eks list-access-entries` shows all three principals.
- All 6 ECR repositories exist with scan-on-push enabled.
- Budget appears in `aws budgets describe-budgets`.

**Test:** Both apply phases complete without errors; all post-apply checks pass.
