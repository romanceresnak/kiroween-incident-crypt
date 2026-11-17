# memory-and-ui — Tasks

Local tasks only: no AWS change, no Terraform change, no push. Execution notes and stop
conditions are those of `agents-core/tasks.md`. Commit after each task.

## TASK-01 — Postmortems and sync script
REQ-MEM-01, MEM-02. **Test:** sections and sidecars for all 8; script with fake
`aws`/`terraform`: account guard, upload, ingestion wait (complete, failed, timeout),
`--delete` opt-in; shellcheck.

## TASK-02 — Shared models
§1. **Test:** new fields, limits, defaults; existing suites pass.

## TASK-03 — incident-memory agent
REQ-MEM-03 to MEM-05. **Test:** query building, result mapping (metadata and fallback),
`TOP_K`/`MIN_SCORE`, Retrieve error → 503, metrics, no model or Kubernetes import.

## TASK-04 — Orchestrator memory and metrics
REQ-MEM-06, MEM-07, MON-05. **Test:** memory queried alongside classification; result in
record and passed to the specialist; failure/missing URL → empty; correlated incidents not
queried; metrics recorded; prompts mention the hints rule.

## TASK-05 — Approval flow
REQ-APR-01 to APR-05. **Test:** state rules and 409s; content-type and Origin checks;
rendering of each proposal kind from the chaos scenarios (strategic and JSON patch);
dry run; GitHub calls against a mock (branch, file, PR) and a GitHub error.

## TASK-06 — UI
REQ-UI-01 to UI-05. **Test:** `/ui` served with CSP; `GET /incidents` order and limit; the
JavaScript never assigns `innerHTML`/`outerHTML` or uses `eval`; every asset is local;
the six characters are present.

## TASK-07 — Chart
REQ-MEM-05, MON-03, MON-04, MON-06, APR-05, UI-05. **Test:** render tests for the new
Deployment, Service, SA, policies, env, optional secret, ServiceMonitors, dashboards JSON
(valid; each metric exists in code); helm lint.

## TASK-08 — Monitoring Application
REQ-MON-01, MON-02. **Test:** Application manifest checks; `helm template` of the pinned
chart with these values renders and has no Alertmanager, no Ingress, no default password.

## TASK-09 — CI
REQ-CIX-01. **Test:** workflow tests; actionlint + shellcheck.

## TASK-10 — Spec status
Record the task table and what was not verified.

## Status

All tasks done. Nothing pushed. Final run: shared 171, orchestrator 185, cpu_memory 41,
network 21, deploy_rollout 17, storage 21, incident_memory 13, repo-level (gitops, chaos,
tools, scripts) 185; ruff clean everywhere; helm lint, actionlint and shellcheck clean.

| Task | Commit | Result |
|---|---|---|
| Spec documents | `8a8119f` | — |
| TASK-01 Postmortems and sync script | `4232587` | 32 tests (fake aws/terraform); shellcheck |
| TASK-02 Shared models | `6633cd1` | shared 171 (+11) |
| TASK-03 incident-memory agent | `a607e3f` | 13 tests (botocore Stubber) |
| TASK-04 Orchestrator memory and metrics | `67d3403` | orchestrator 137 (+11), cpu_memory 41 |
| TASK-05 Approval flow | `2ac99a7` | orchestrator 164 (+27) |
| TASK-06 UI | `6e45048` | orchestrator 185 (+21); `node --check` ok |
| TASK-07 Chart | `56200c5` | helm lint; repo-level 177 |
| TASK-08 Monitoring Application | `150c10f` | 7 tests incl. a render of 92.21.2 for 1.37 |
| TASK-09 CI | `03a61ca` | actionlint + shellcheck; repo-level 185 |
| TASK-10 Status | this commit | — |

Changes from the design or the request:
- The monitoring Application uses the HTTPS chart repository, like `metrics-server.yaml`;
  an OCI source would need a repository registered in Argo CD.
- Alertmanager is off (no alert routes are in scope) and Grafana needs a `grafana-admin`
  Secret created by the operator: the chart otherwise generates a new random password on
  every Argo CD render.
- The watcher now excludes `monitoring`.
- CI now shellchecks every chaos script; before, only `chaos/oom/oom.sh` was checked.

Not verified:
- No image was built locally (no Docker daemon); CI builds them after push.
- Bedrock `Retrieve` on `VIBAT27SDH` was never called. Unknown: whether the caller needs
  more than `bedrock:Retrieve` with S3 Vectors, whether the sidecar metadata comes back in
  results, and whether `MIN_SCORE` 0.3 suits Titan v2 scores.
- `scripts/sync-postmortems.sh` never ran against AWS.
- kube-prometheus-stack never ran on the cluster: fit on 2x t3.large Spot, the admission
  webhook jobs, scraping through the agents' NetworkPolicies, the dashboard in Grafana.
- The UI was never opened in a browser; only static checks and `node --check`.
- The GitHub path ran only against a mock. A real token needs Contents and Pull requests
  write on the repository; branch protection may block the PR.
- The remediation file lands in `gitops/remediations/`, which no Application applies. The
  PR records the approved change; applying it needs the workload's manifests in `/gitops`.
  The patch files were not run through `kustomize build`.
- `Incident.similar_incidents` is a new field and the models reject unknown fields: during
  the rollout an old specialist pod returns 422 to a new orchestrator.
- Approve/reject have no authentication: anyone who can port-forward to the orchestrator
  can decide.
- The workflow triggers only on `agents/**`, `tools/**` and the workflow file, so a
  chart-only or script-only commit runs no CI tests.
