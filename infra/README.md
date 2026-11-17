# infra

Terraform for the Kiroween Incident Crypt EKS platform (`eu-west-1`).
Spec: `.kiro/specs/infra/`.

## Layout

| Path | Purpose |
|---|---|
| `bootstrap/` | Local-state module that creates the S3 state bucket. Applied once. |
| `main.tf`, `variables.tf`, `outputs.tf` | Root stack: providers, S3 backend, module calls. |
| `modules/` | `vpc`, `ecr`, `eks`, `github_oidc`, and the modules added by later tasks. |

## Usage

1. Apply `bootstrap/` once. Its state is local and must not be committed
   (`*.tfstate*` is in the repo `.gitignore`).
2. Initialise the main stack with the bucket name (the `bucket` attribute is
   deliberately absent from the backend block):

   ```
   terraform init -backend-config=bucket=kiroween-prod-tf-state-<account_id>
   ```

3. Supply the required variables through the environment, never a committed
   `terraform.tfvars`:

   ```
   export TF_VAR_admin_iam_principal_arn=arn:aws:iam::<account_id>:role/<admin-role>
   export TF_VAR_budget_alert_email=<address>
   ```

   `admin_iam_principal_arn` must be a well-formed ARN. A placeholder such as
   `dummy` fails the provider's ARN validation, even for `plan`.

`.terraform.lock.hcl` is committed.

## GitHub OIDC provider

An AWS account can hold one OIDC provider per URL. By default the
`github_oidc` module looks up the existing `token.actions.githubusercontent.com`
provider. In an account that has none, set
`create_github_oidc_provider = true`.

## Known trade-offs

### The apply role can modify itself

The GitHub Actions apply role (`kiroween-<env>-gha-apply`) has `iam:*` on roles
and policies named `kiroween-<env>-*`. That prefix includes the apply role and
the plan role themselves. A workflow running on `main` can therefore grant
itself broader permissions.

This is accepted for this project. Anyone who can push to `main` already
controls the pipeline, so the deny would not change who can do harm, and it
would block the pipeline from updating its own roles.

If the trust boundary changes (more contributors, a protected-branch bypass, a
shared account), revisit this. Options, in order of effort:

- Require reviewed pull requests and a protected `main` branch.
- Add an explicit `Deny` on IAM write actions against the two `gha-*` roles and
  change those roles with a manual admin apply.
- Attach a permissions boundary to every role the pipeline creates.

### Service-wide permissions on the apply role

`ec2`, `eks`, `bedrock`, `budgets` and `logs` are granted service-wide, because
some of their actions do not support resource-level scoping and a missing
permission would only surface during the first apply. `iam`, `s3`, `s3vectors`
and `ecr` are scoped to the project name prefix.
