# Infra Spec — Requirements

> EARS format. Each requirement follows one of:
> - **Ubiquitous**: The \<system\> shall \<action\>.
> - **Event-driven**: When \<trigger\>, the \<system\> shall \<action\>.
> - **State-driven**: While \<state\>, the \<system\> shall \<action\>.
> - **Option**: Where \<feature is included\>, the \<system\> shall \<action\>.
> - **Unwanted**: If \<condition\>, the \<system\> shall \<action\>.

---

## 1. Bootstrap Module (Remote State Pre-requisite)

**REQ-BS-01 (Ubiquitous)**
The Terraform configuration shall include an `infra/bootstrap/` module that uses **local state** and whose sole responsibility is creating the S3 state bucket (with versioning and SSE enabled) and any KMS key required by the main stack.

**REQ-BS-02 (Ubiquitous)**
The `infra/bootstrap/` module shall be applied once before the main stack; its output (bucket name) shall be passed into the main stack's backend configuration so that `terraform init` succeeds.

**REQ-BS-03 (Ubiquitous)**
No resource in `infra/bootstrap/` shall depend on the EKS cluster, VPC, or any resource that the main stack creates.

---

## 2. Remote State (Main Stack)

**REQ-RS-01 (Ubiquitous)**
The main Terraform configuration shall store state in the S3 bucket created by `infra/bootstrap/`, in region `eu-west-1`.

**REQ-RS-02 (Ubiquitous)**
The S3 backend shall use native S3 state locking (`use_lockfile = true`) without a DynamoDB table.

**REQ-RS-03 (Ubiquitous)**
The state bucket shall have versioning enabled and server-side encryption (SSE-S3 or SSE-KMS).

**REQ-RS-04 (Unwanted)**
If the Terraform state backend is not explicitly configured, Terraform shall fail rather than fall back to local state.

---

## 3. VPC

**REQ-VPC-01 (Ubiquitous)**
The Terraform configuration shall provision a single VPC in `eu-west-1` spanning exactly two Availability Zones.

**REQ-VPC-02 (Ubiquitous)**
The VPC shall contain one public subnet and one private subnet per Availability Zone (four subnets total).

**REQ-VPC-03 (Ubiquitous)**
The VPC shall deploy exactly one NAT gateway (single-AZ) in a public subnet to minimise cost.

**REQ-VPC-04 (Ubiquitous)**
Private subnets shall route outbound internet traffic through the single NAT gateway.

**REQ-VPC-05 (Ubiquitous)**
Public subnets shall route outbound internet traffic directly through an Internet Gateway.

**REQ-VPC-06 (Ubiquitous)**
Subnets and route tables shall be tagged in a way that allows EKS to discover them for load balancer provisioning (`kubernetes.io/role/elb` and `kubernetes.io/role/internal-elb` tags).

---

## 4. EKS Cluster

**REQ-EKS-01 (Ubiquitous)**
The Terraform configuration shall provision an EKS cluster running the latest supported Kubernetes minor version available in `eu-west-1` at the time of `terraform apply`.

**REQ-EKS-02 (Ubiquitous)**
The EKS cluster shall use **API authentication mode** (`access_config.authentication_mode = "API"`).

**REQ-EKS-03 (Ubiquitous)**
Terraform shall create an EKS access entry granting `AmazonEKSClusterAdminPolicy` to an IAM principal supplied via variable (`var.admin_iam_principal_arn`).

**REQ-EKS-04 (Ubiquitous)**
Terraform shall create an EKS access entry granting `AmazonEKSClusterAdminPolicy` to the GitHub Actions apply role (REQ-GHA-05).

**REQ-EKS-05 (Ubiquitous)**
The EKS cluster shall have exactly one managed node group with the following capacity configuration:
- Instance types: `t3.large`, `t3a.large`, `m5.large` (Spot, in preference order)
- Capacity type: SPOT
- Scaling: `min_size = 2`, `desired_size = 2`, `max_size = 3`

**REQ-EKS-06 (Ubiquitous)**
The EKS cluster shall have the **EKS Pod Identity** add-on installed and enabled.

**REQ-EKS-07 (Ubiquitous)**
The EKS cluster shall have the **Amazon EBS CSI Driver** add-on installed to support PersistentVolumeClaims backed by EBS.

**REQ-EKS-08 (Ubiquitous)**
The EKS cluster control plane shall log to CloudWatch for `api`, `audit`, and `authenticator` log types. The CloudWatch log group retention shall be set to **7 days**.

**REQ-EKS-09 (State-driven)**
While a node group update is in progress, the cluster shall use a rolling update strategy that maintains at least one ready node.

**REQ-EKS-10 (Ubiquitous)**
The Amazon VPC CNI shall be managed by Terraform as an `aws_eks_addon` with the configuration `enableNetworkPolicy = "true"`, adopting the cluster's existing self-managed installation, so that Kubernetes `NetworkPolicy` objects are enforced.

---

## 5. Argo CD Bootstrap

**REQ-ACD-01 (Ubiquitous)**
Terraform shall install Argo CD into the EKS cluster using a `helm_release` resource targeting the `argocd` namespace.

**REQ-ACD-02 (Ubiquitous)**
The `helm_release` shall pin to an explicit Argo CD chart version; the version shall be a variable with a documented default.

**REQ-ACD-03 (Ubiquitous)**
After the `helm_release` is applied, Terraform shall create one root Argo CD `Application` resource (app-of-apps pattern) pointing at the `gitops/apps` directory (inside `/gitops`) in the `romanceresnak/kiroween-incident-crypt` GitHub repository, tracking the `main` branch. That directory holds only Argo CD `Application` manifests.

**REQ-ACD-04 (Ubiquitous)**
All further Kubernetes resources (agents, monitoring, etc.) shall be deployed exclusively by Argo CD from `/gitops` — not by Terraform.

**REQ-ACD-05 (Ubiquitous)**
The Argo CD initial admin password shall not be overridden by Terraform; the default `argocd-initial-admin-secret` Secret generated by Argo CD shall be used.

---

## 6. ECR Repositories

**REQ-ECR-01 (Ubiquitous)**
Terraform shall create one ECR repository per agent: `orchestrator`, `cpu-memory`, `network`, `deploy-rollout`, `storage`, and `incident-memory`.

**REQ-ECR-02 (Ubiquitous)**
Each ECR repository shall have image scanning on push enabled.

**REQ-ECR-03 (Ubiquitous)**
Each ECR repository shall have a lifecycle policy that retains only the 10 most-recent tagged images per repository to control storage cost.

**REQ-ECR-04 (Ubiquitous)**
ECR repository names shall be parameterised so the list of agents can be extended via a variable without changing resource definitions.

**REQ-ECR-05 (Ubiquitous)**
Each ECR repository shall have `image_tag_mutability = "IMMUTABLE"`, so a pushed tag, including a commit-SHA tag, can never be overwritten.

---

## 7. Postmortem Storage and Bedrock Knowledge Base

**REQ-KB-01 (Ubiquitous)**
Terraform shall create an S3 bucket in `eu-west-1` to store postmortem documents, with versioning enabled and public access blocked.

**REQ-KB-02 (Ubiquitous)**
Terraform shall create an S3 Vectors bucket (`aws_s3vectors_vector_bucket`) and a vector index (`aws_s3vectors_index`) in `eu-west-1` with the following index configuration:
- `dimension = 1024`
- `distance_metric = "cosine"`
- `non_filterable_metadata_keys = ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]`

**REQ-KB-03 (Ubiquitous)**
Terraform shall create an Amazon Bedrock Knowledge Base (`aws_bedrockagent_knowledge_base`) with:
- `storage_configuration.type = "S3_VECTORS"` referencing the vector bucket and index from REQ-KB-02
- Embedding model: **Amazon Titan Text Embeddings V2** (`amazon.titan-embed-text-v2:0`)
  - Confirmed available in `eu-west-1` per the [Bedrock Knowledge Bases supported models page](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-supported.html)
  - Supported dimensions for this model: 256, 512, 1024 — this spec uses 1024

**REQ-KB-04 (Ubiquitous)**
The Bedrock Knowledge Base shall use the postmortem S3 bucket (REQ-KB-01) as its data source via an `aws_bedrockagent_data_source` resource.

**REQ-KB-05 (Ubiquitous)**
The Knowledge Base service role shall have exactly the following IAM permissions, and no others beyond the trust policy:

| Action | Resource | Purpose |
|---|---|---|
| `s3vectors:PutVectors` | vector index ARN | Write embeddings during ingestion |
| `s3vectors:QueryVectors` | vector index ARN | Similarity search during retrieval |
| `s3vectors:GetVectors` | vector index ARN | Fetch vectors during retrieval |
| `s3vectors:DeleteVectors` | vector index ARN | Remove stale chunks on re-sync |
| `s3vectors:GetIndex` | vector index ARN | Read index metadata |
| `s3:GetObject` | postmortem bucket ARN + `/*` | Read source documents |
| `s3:ListBucket` | postmortem bucket ARN | List source documents |
| `bedrock:InvokeModel` | Titan V2 model ARN | Generate embeddings |

> Action names are taken from the [S3 Vectors service authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_s3vectors.html). `PutVectors`, `QueryVectors`, `GetVectors`, `DeleteVectors`, and `GetIndex` are confirmed as the correct IAM action names.

**REQ-KB-06 (Ubiquitous)**
The AWS Terraform provider version shall be pinned to `>= 6.27.0` (the minimum version that includes `aws_s3vectors_vector_bucket` and `aws_s3vectors_index`).

---

## 8. IAM — Pod Identity Roles

**REQ-IAM-01 (Ubiquitous)**
Terraform shall create one IAM role per agent (`orchestrator`, `cpu-memory`, `network`, `deploy-rollout`, `storage`, `incident-memory`) for use with EKS Pod Identity.

**REQ-IAM-02 (Ubiquitous)**
Each Pod Identity role's trust policy shall allow `pods.eks.amazonaws.com` to call `sts:AssumeRole` and `sts:TagSession`, scoped to the specific EKS cluster ARN and agent service account.

**REQ-IAM-03 (Ubiquitous)**
Kubernetes RBAC for in-cluster read-only access (ServiceAccount, Role, RoleBinding) is out of scope for this Terraform spec — it is managed by the Helm chart in `/gitops/charts/agents/`. Each Pod Identity IAM role shall grant only:
- `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` on:
  - The EU cross-region inference profile ARN (`arn:aws:bedrock:eu-west-1:<account>:inference-profile/eu.*`)
  - The underlying foundation-model ARNs in all EU regions the profile routes to (`arn:aws:bedrock:eu-*::foundation-model/*`)

**REQ-IAM-04 (Ubiquitous)**
The `incident-memory` agent's Pod Identity role shall additionally allow `bedrock:Retrieve` scoped to the Knowledge Base ARN from REQ-KB-03.

**REQ-IAM-05 (Ubiquitous)**
Pod Identity associations (`aws_eks_pod_identity_association`) shall be created by Terraform, binding each agent's IAM role to its Kubernetes service account name and the `agents` namespace.

---

## 9. IAM — GitHub Actions OIDC

**REQ-GHA-01 (Ubiquitous)**
Terraform shall configure an IAM OIDC provider for `token.actions.githubusercontent.com`.

**REQ-GHA-02 (Ubiquitous)**
Terraform shall create a **plan role** that trusts the OIDC provider with subject condition `repo:romanceresnak@25537084/kiroween-incident-crypt@1461265969:pull_request` (GitHub immutable subject format; see REQ-GHA-10).

**REQ-GHA-03 (Ubiquitous)**
The plan role shall have the following permissions:
- Read-only describe/list/get access to EC2, EKS, IAM, S3, ECR, Bedrock, and S3 Vectors resources (sufficient for `terraform plan`).
- `s3:GetObject` on the state bucket (to read current state).
- `s3:PutObject` and `s3:DeleteObject` on keys matching `*.tflock` in the state bucket only (required by `use_lockfile` state locking during plan).
- No write permissions on any other resource.

**REQ-GHA-04 (Ubiquitous)**
Terraform shall create an EKS access entry for the plan role, associating `AmazonEKSAdminViewPolicy` at cluster scope. This grants `get/list/watch` on all Kubernetes resource types cluster-wide, which is the minimum needed by the Terraform `helm` and `kubernetes` providers to read existing release state without write access.

> `AmazonEKSAdminViewPolicy` (ARN: `arn:aws:eks::aws:cluster-access-policy/AmazonEKSAdminViewPolicy`) is confirmed as the correct policy — it is the cluster-scoped read-only equivalent of `AmazonEKSAdminPolicy`, covering all API groups with `get/list/watch` only. `AmazonEKSViewPolicy` is namespace-scoped and would require one association per namespace.

**REQ-GHA-05 (Ubiquitous)**
Terraform shall create an **apply role** that trusts the OIDC provider with subject condition `repo:romanceresnak@25537084/kiroween-incident-crypt@1461265969:ref:refs/heads/main`.

**REQ-GHA-06 (Ubiquitous)**
The apply role shall have permissions for `terraform apply` operations (create/update/delete across EC2, EKS, IAM, S3, ECR repositories, Bedrock, and S3 Vectors resources). It shall not have image push or ECR login permissions; those belong to the ECR push role (REQ-GHA-08).

**REQ-GHA-07 (Ubiquitous)**
No role shall use wildcard (`*`) subjects in the OIDC trust condition; the plan, apply and ECR push roles shall each use a fully specified subject.

**REQ-GHA-08 (Ubiquitous)**
Terraform shall create an **ECR push role** that trusts the OIDC provider with subject condition `repo:romanceresnak@25537084/kiroween-incident-crypt@1461265969:ref:refs/heads/main`. The role shall allow only `ecr:GetAuthorizationToken` on all resources, and `ecr:BatchCheckLayerAvailability`, `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`, `ecr:PutImage` and `ecr:DescribeImages` on repositories named `kiroween/*`. `ecr:DescribeImages` lets the image workflow detect that a tag already exists, which matters because the repositories are immutable (REQ-ECR-05). It shall have no other permissions, and no access entry in the EKS cluster.

**REQ-GHA-10 (Ubiquitous)**
The trust subjects shall use GitHub's immutable subject format, `repo:<owner>@<owner_id>/<repo>@<repo_id>:...`, set through `var.gha_oidc_subject_prefix`. This repository issues immutable subjects (`use_immutable_subject: true`), so a name-only subject never matches and every role assumption fails.

**REQ-GHA-09 (Ubiquitous)**
Terraform shall output the ECR push role ARN as `gha_ecr_push_role_arn`.

---

## 10. FinOps

**REQ-FIN-01 (Ubiquitous)**
The AWS provider configuration shall set `default_tags` with at minimum:
```
Project  = "kiroween-incident-crypt"
ManagedBy = "terraform"
```
These tags shall propagate automatically to all taggable resources.

**REQ-FIN-02 (Ubiquitous)**
Terraform shall create an AWS Budget with a monthly cost limit set by `var.budget_limit_usd` (default **120 USD**), sending an email notification to an address supplied via variable (`var.budget_alert_email`) when actual spend exceeds the limit.

**REQ-FIN-03 (Ubiquitous)**
All CloudWatch log groups created by Terraform (EKS control plane, any Lambda, etc.) shall have a retention period of **7 days**.

---

## 11. General Conventions

**REQ-GEN-01 (Ubiquitous)**
All Terraform resource values shall be driven by `variables.tf`; no region, account ID, bucket name, or other environment-specific string shall be hardcoded in resource definitions.

**REQ-GEN-02 (Ubiquitous)**
The Terraform configuration shall define outputs for: EKS cluster name, EKS cluster endpoint, ECR repository URLs, Knowledge Base ID, GitHub Actions plan role ARN, GitHub Actions apply role ARN, and GitHub Actions ECR push role ARN.

**REQ-GEN-03 (Ubiquitous)**
No static AWS credentials shall appear in any Terraform file; all AWS access from GitHub Actions shall use the OIDC roles (REQ-GHA-02, REQ-GHA-04).
