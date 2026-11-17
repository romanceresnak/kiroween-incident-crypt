# memory-and-ui — Requirements

> EARS format, as in `agents-core`. Earlier specs are cited as `core:`, `spx:`
> (specialists) and `hard:` (hardening).

## Scope

Four additions on top of the running platform, following the existing agents and chart.

| Part | What |
|---|---|
| A. Incident memory | Seeded postmortems, a sync script, the `incident-memory` agent (Bedrock KB Retrieve), and similar incidents in every investigation |
| B. Approval | Approve/reject on the orchestrator; an approved proposal becomes a rendered `/gitops` patch and PR (GitHub API when a token exists, otherwise dry run) |
| C. Monitoring | kube-prometheus-stack via Argo CD, ServiceMonitors on, Grafana dashboards as code |
| D. UI | A static Halloween UI served by the orchestrator at `/ui`, reached by port-forward |

Facts this spec relies on (read 17 Nov 2026, no change made):
- Knowledge Base `VIBAT27SDH` (Titan Text Embeddings v2, 1024 dims, S3 Vectors), source
  bucket `kiroween-prod-postmortems-255834079310`, one S3 data source over the whole bucket.
- The `incident-memory-sa` Pod Identity role has `bedrock:Retrieve` on that Knowledge Base
  (`infra/modules/pod_identity`, REQ-IAM-04). ECR repo `kiroween/incident-memory` exists.
- kube-prometheus-stack 92.21.2 resolves from `oci://ghcr.io/prometheus-community/charts`
  and from https://prometheus-community.github.io/helm-charts, and supports
  Kubernetes >= 1.25.

Out of scope: Terraform or IAM changes, a public ingress, authentication beyond
port-forward access, applying approved changes to the cluster (Argo CD applies a merged
PR, as before), persistence of incidents.

## A. Incident memory (MEM)

**REQ-MEM-01** `docs/postmortems/` shall hold 8 postmortems: OOMKilled, CPU throttling,
NetworkPolicy block, CoreDNS failure, ImagePullBackOff, CrashLoopBackOff from a bad
ConfigMap, PVC without a StorageClass, node disk pressure. Each has the sections Summary,
Impact, Root cause, Evidence, Fix, Prevention, and a `<file>.metadata.json` sidecar with
`title`, `category`, `signal` and a one-paragraph `fix` (Bedrock KB metadata format).
They describe fictional services; no real customer data.

**REQ-MEM-02** `scripts/sync-postmortems.sh` shall upload `docs/postmortems/` to the
postmortem bucket, start an ingestion job on the Knowledge Base's data source, and wait
until it is `COMPLETE` (exit 0) or `FAILED`/timed out (exit 1). Bucket and KB ID come from
env (`POSTMORTEM_BUCKET`, `KNOWLEDGE_BASE_ID`) or `terraform output`; the data source ID
from `bedrock-agent list-data-sources`. Deleting objects not in git requires `--delete`.
It shall refuse to run against an account other than `EXPECTED_ACCOUNT_ID`
(default 255834079310), be shellcheck clean, and have tests with a fake `aws`.

**REQ-MEM-03** `agents/incident_memory` shall serve `POST /similar` (an `Incident`) and
return up to `TOP_K` (default 3) `SimilarIncident`s scored by Bedrock `Retrieve` on
`KNOWLEDGE_BASE_ID`, dropping results below `MIN_SCORE` (default 0.3). Each result has
title, category, score, the fix that worked, source and a short excerpt; title, category
and fix come from the metadata sidecar, falling back to the chunk text.

**REQ-MEM-04** The query text shall be built by code from the incident's reason, workload
kind and message, capped at 1000 characters. Retrieve errors return HTTP 503; the
agent never calls a model and has no Kubernetes permissions.

**REQ-MEM-05** The agent runs as `incident-memory-sa` (existing Pod Identity role), on
8080/9090, with a Dockerfile like the others, and counts requests and results in
Prometheus metrics.

**REQ-MEM-06 (Event-driven)** When the orchestrator processes an incident, it shall query
incident-memory at the same time as it classifies, store the result in the record as
`similar_incidents`, and pass it to the specialist inside the incident. A failure or a
missing `INCIDENT_MEMORY_URL` gives an empty list and never fails the incident.

**REQ-MEM-07** Specialist prompts shall treat similar incidents as hints to check with
tools, never as evidence. Evidence still has to come from a tool.

## B. Approval (APR)

**REQ-APR-01** The orchestrator shall expose `POST /incidents/<id>/approve` and
`POST /incidents/<id>/reject` with a JSON body `{"by": "<name>", "comment": "..."}`.
They apply only to an incident in `awaiting_approval` (otherwise HTTP 409) and record
the decision, who, when and the comment. Status becomes `approved` or `rejected`.

**REQ-APR-02** The endpoints shall accept only `application/json` and refuse a request
whose `Origin` is not the orchestrator's own host (HTTP 403), so a web page the operator
visits cannot approve through the port-forward.

**REQ-APR-03 (Event-driven)** On approve, the orchestrator shall render the proposal as a
file under `gitops/remediations/<namespace>/<kind>-<name>.yaml`: a Kustomize strategic
merge patch when the field path has only map keys and `[name=…]` list selectors, otherwise
an RFC 6902 JSON patch with a `test` of the current value before the `replace`. It renders
a PR title, branch `remediation/<incident-id>` and a body with the incident, diagnosis,
evidence, the change, risk, similar incidents and the approver.

**REQ-APR-04** If `GITHUB_TOKEN` is set, the orchestrator shall create the branch, commit
the file and open the PR on `GITHUB_REPOSITORY` against `GITHUB_BASE_BRANCH` (default
`main`) and record its URL. Without a token it records a dry run with the rendered PR. A
GitHub error is recorded on the approval and the incident stays `approved`.

**REQ-APR-05** The token comes from an optional Secret `orchestrator-github` (key `token`)
that the operator creates; the chart never contains it. Nothing else changes the
cluster: the agents stay read-only.

## C. Monitoring (MON)

**REQ-MON-01** `gitops/apps/monitoring.yaml` shall install kube-prometheus-stack 92.21.2
into `monitoring` with: Prometheus retention 2 days (and a size cap), emptyDir storage,
small requests and limits that fit next to the agents on 2x t3.large, Alertmanager off,
scraping of EKS-managed control-plane components off, and ServiceMonitors selected from
every namespace.

**REQ-MON-02** Grafana shall have no default password: the admin credentials come from a
Secret `grafana-admin` the operator creates, anonymous access is off, and there is no
ingress.

**REQ-MON-03** The agents chart shall enable its ServiceMonitors, tolerant of the CRD not
existing yet on the first sync.

**REQ-MON-04** The agents chart shall ship Grafana dashboards as a ConfigMap labelled
for the Grafana sidecar, with panels for cost per agent, cost per incident, tokens in/out,
incidents by category, investigation latency and the transient (false-alarm) rate. Every
metric a panel uses shall exist in the code.

**REQ-MON-05** The orchestrator shall export `orchestrator_incidents_total{category,status,
outcome}` and `orchestrator_investigation_seconds{category}` (histogram).

**REQ-MON-06** The watcher shall exclude the `monitoring` namespace by default.

## D. UI (UI)

**REQ-UI-01** The orchestrator shall serve a static UI at `/ui` from files in its package
(no new image, repo or build step), plus `GET /incidents` returning the most recent
records newest first.

**REQ-UI-02** The UI shall show a live incident feed (polling) and, for a selected
incident, its classification, evidence, proposal, validation notes, similar past
incidents, related incidents, cost and tokens per agent, and Approve/Reject buttons that
ask for the approver's name.

**REQ-UI-03** The theme is Halloween with original art only, as inline SVG drawn for this
project: Necromancer (orchestrator), Vampire (cpu-memory), Spider (network), Zombie
(deploy-rollout), Ghost (storage), Archivist (incident-memory). No copyrighted characters,
no external fonts, images or scripts.

**REQ-UI-04** Cluster text (messages, evidence, logs) shall be rendered as text only,
never as HTML. `/ui` responses carry a Content-Security-Policy that allows only same-origin
scripts and styles.

**REQ-UI-05** The UI is reached only through `kubectl port-forward`; the chart adds no
Service type other than ClusterIP and no Ingress.

## CI (CIX)

**REQ-CIX-01** The workflow shall test, build and push `incident_memory` to
`kiroween/incident-memory` and set its image tag in `update-chart`.
