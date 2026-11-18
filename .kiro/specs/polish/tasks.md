# polish — Tasks

Execution notes and stop conditions are those of `agents-core/tasks.md`. Commit after each
task; do not push. Pre-approved: Terraform changes that only add or update IAM policies in
place (plan first; apply only with 0 destroy and 0 replace; verify with the simulator).

## TASK-01 — UI animation
REQ-ANI-01 to ANI-05. Already delivered in `b1e6824` and `3105f04` and pushed.
**Done when:** the DOM tests (28) still pass. No new commit.

## TASK-02 — incident-memory: category filter, fallback, configurable minimum score
REQ-RET-02 to RET-06, REQ-LRN-08 (the `origin` field).
`SimilarIncident.origin`; `Memory.similar(incident, category)`; `POST /similar?category=`;
`MIN_SCORE` validation and metrics. Pick the default `MIN_SCORE` from live scores if AWS
access is available, otherwise record the provisional value and why.
**Test:** Stubber requests with and without the filter; fallback after a ValidationException;
code-side category check (other category, `general`, no category); `unknown` unfiltered;
score drop; metrics; invalid `MIN_SCORE`; origin from metadata and from the key.

## TASK-03 — orchestrator: classify first, then look up by category
REQ-RET-01, REQ-RET-06, REQ-IAM-03 (min score).
`IncidentService` and `MemoryClient` take the category; chart `incidentMemory.minScore`.
**Test:** the lookup receives the classified category; it runs after classification; unknown
passes no category; existing memory tests updated on purpose.

## TASK-04 — learning: render, write, ingest, record
REQ-LRN-01 to LRN-07.
`Learning` model, `Approval.learning`, `learning.py`, service integration, metrics, `main`
wiring.
**Test:** document and sidecar match the seeded format; hostile text cannot add a heading;
object keys and order; ingestion token; every failure path leaves the approval intact
(S3 error, ingestion error, conflict, timeout, not configured); rejection writes nothing.

## TASK-05 — UI: mark learned postmortems, show the learning outcome
REQ-LRN-08. **Test:** DOM tests for the badges and for the outcome lines.

## TASK-06 — chart: learning settings
REQ-IAM-03. Orchestrator env, values (`learning.*`, data source ID), render tests.

## TASK-07 — Terraform: orchestrator IAM for learning
REQ-IAM-01, IAM-02. Needs a valid AWS session.
`terraform fmt -check`, `validate`, `plan`; apply only if the plan has IAM policy creates or
in-place updates only; then the simulator checks.

## TASK-08 — Spec status
Record the task table and what was not verified.

## Status

Nothing pushed. TASK-07 is **only partly done**: the Terraform source is written and passes
`fmt` and `validate`, but it has not been planned or applied and the simulator check has not
run, because the AWS session had expired (`aws login`) for the whole session.

Final run: shared 171, orchestrator 236, cpu_memory 41, network 21, deploy_rollout 17,
storage 21, incident_memory 48, repo-level (gitops, chaos, tools, scripts) 197, UI DOM 34;
ruff, helm lint, terraform fmt/validate, shellcheck and actionlint clean.

| Task | Commit | Result |
|---|---|---|
| Spec documents | `0333eb2` | — |
| TASK-01 UI animation | `b1e6824`, `3105f04` (before this spec, pushed) | 28 DOM tests still pass; no new commit |
| TASK-02 Category filter, fallback, minimum score | `ecd3ea1` | incident_memory 48 (+35) |
| TASK-03 Classify first, look up by category | `ae57b8c` | orchestrator 195, gitops 65 |
| TASK-04 Learn a postmortem on approval | `fe3fad8` | orchestrator 236 (+41) |
| TASK-05 UI: learned badge and learning outcome | `ff568be` | DOM 34 (+6) |
| TASK-06 Chart: learning settings | `c040639` | gitops 67 |
| TASK-07 Terraform IAM (source only) | `eb45873` | fmt, validate, 5 guard tests; plan/apply/simulator NOT done |
| TASK-07 IAM simulator script | `7867ac6` | 8 tests with a fake aws |
| TASK-08 Status | this commit | — |

Existing tests changed on purpose: the two tests that encoded "memory runs while the
incident is classified" now assert the new order (classify, then look up by category); the
incident-memory Stubber helper expects the candidate pool instead of `top_k`; the chart test
expects `MIN_SCORE` 0.5.

### Open: needs `aws login`

1. **Data source ID.** `aws bedrock-agent list-data-sources --knowledge-base-id VIBAT27SDH
   --region eu-west-1 --query 'dataSourceSummaries[].dataSourceId' --output text`, then set
   `orchestrator.learning.dataSourceId` in `gitops/charts/agents/values.yaml` and commit.
   Until then approvals record "learning is not configured" and nothing is written.
2. **Terraform.** `cd infra && terraform init -backend-config=bucket=kiroween-prod-tf-state-255834079310`
   (with `TF_VAR_admin_iam_principal_arn` and `TF_VAR_budget_alert_email` set as before),
   then `terraform plan -out=/tmp/learn.tfplan`. Expected: `Plan: 1 to add, 0 to change, 0 to
   destroy`, the one resource being `module.pod_identity.aws_iam_role_policy.learn["orchestrator"]`.
   Apply only if that is what it shows. If anything else changed or any destroy or replace
   appears, stop: that is not the pre-approved change.
3. **Simulator.** `scripts/verify-learning-iam.sh` must print `all checks match`.
4. **Minimum score.** The default 0.5 is provisional. Measure the live scores of the three
   chaos scenarios and of the unrelated ones with the filter on and off, then set
   `incidentMemory.minScore`.

### Not verified

- Whether Retrieve accepts the `orAll` metadata filter on this S3 Vectors Knowledge Base.
  If not, incident-memory falls back to an unfiltered query and filters in code (tested with
  a stub, never against AWS). `incident_memory_filter_fallback_total` shows which happens.
- Whether `ConflictException` is what Bedrock returns when an ingestion job is already
  running. Any other error is recorded as `written/failed`, and the document is kept.
- How long an ingestion job takes before a learned postmortem can be retrieved. The UI says
  "once indexing finishes".
- The S3 and Bedrock calls ran only against botocore's Stubber, never against AWS.
- The new UI (badges, Memory line) has not been looked at in a browser; DOM tests only.
- The orchestrator now waits for one more Retrieve call (about a second) between
  classification and delegation.
- Learned text comes from the cluster. The defences (approval, bounded single-line fields,
  the specialists treating hints as non-evidence, a write-only `learned/` prefix) are tested
  but cannot make a poisoned learned postmortem impossible.
- A learned postmortem records an approved proposal. It says so; nothing checks later that
  the pull request was merged or that the fix worked.
- Deleting a learned postmortem means deleting its two S3 objects and re-ingesting.
- If the Knowledge Base data source is ever recreated, its ID changes and the chart value
  goes stale.
- Rollout: `SimilarIncident` and `Approval` gained fields and the models reject unknown
  fields, so during a rollout an old pod can return 422 to a new one. Run the scenarios
  after every pod in `agents` has the new tag.
