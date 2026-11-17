# memory-and-ui — Design

## 1. Models (`kiroween_shared.models`)

```python
class SimilarIncident:  title, category | None, score, fix, source, excerpt
Incident.similar_incidents: list[SimilarIncident] = []           # max 5
class PullRequest:      mode ("dry_run" | "github"), title, branch, path, content,
                        body, url | None, error | None
class Approval:         decision ("approved" | "rejected"), by, at, comment,
                        pull_request | None
IncidentRecord.similar_incidents, IncidentRecord.approval
Status += "approved", "rejected"
```

`by` is 1–64 characters of `[A-Za-z0-9 ._@-]`. Defaults keep every existing producer
valid.

## 2. Postmortems and sync (REQ-MEM-01, 02)

`docs/postmortems/NN-<slug>.md` with a fixed heading set, and `NN-<slug>.md.metadata.json`:

```json
{"metadataAttributes": {"title": "...", "category": "storage",
                        "signal": "ProvisioningFailed", "fix": "..."}}
```

S3 Vectors keeps custom metadata inside `AMAZON_BEDROCK_METADATA`, which the index marks
non-filterable, so the 2 KB filterable limit does not apply; the fix stays under 600
characters anyway.

`scripts/sync-postmortems.sh [--delete] [--timeout SECONDS]`:
`aws sts get-caller-identity` → account guard → resolve bucket and KB (env, else
`terraform -chdir=infra output -raw`) → `aws s3 sync docs/postmortems s3://<bucket>/postmortems/
--exclude '*' --include '*.md' --include '*.metadata.json' [--delete]` → data source ID
from `aws bedrock-agent list-data-sources` (exactly one expected) →
`start-ingestion-job` → poll `get-ingestion-job` every 10 s.

## 3. incident-memory agent

```
agents/incident_memory/src/kiroween_incident_memory/
  memory.py     build_query(incident), to_similar(result), Memory(client, kb_id, top_k, min_score)
  api.py        create(memory) -> FastAPI; POST /similar -> list[SimilarIncident]
  __main__.py   boto3 client "bedrock-agent-runtime" (region from AWS_REGION), serve
```

Retrieve call: `retrieve(knowledgeBaseId, retrievalQuery={"text": q},
retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}})`.
Mapping: `content.text` → excerpt (first 400 chars), `metadata` → title/category/fix,
`location.s3Location.uri` → source, `score`. Without metadata, title is the first `# `
heading and fix is the `## Fix` section of the chunk, if present. The call runs in a
thread (`asyncio.to_thread`). Metrics: `incident_memory_requests_total{result}`,
`incident_memory_results_total`.

## 4. Orchestrator

`_process` runs `classify(incident)` and `memory.similar(incident)` with
`asyncio.gather`; memory errors are caught inside the client. The record gets
`similar_incidents`; the specialist receives `incident.model_copy(update=
{"similar_incidents": ...})`. Correlated incidents are never processed, so they are never
queried. Metrics in `kiroween_orchestrator.metrics` on the shared `REGISTRY`; the outcome
label is `none` when there is no investigation.

Prompt addition (common specialist prompt and cpu-memory prompt): "similar_incidents are
past postmortems that may or may not apply. Use them to decide what to check, and confirm
with your tools; never cite them as evidence."

## 5. Approval

```
approval.py
  render_patch(proposal) -> (path, content)        # strategic or JSON 6902
  render_pr(record, by) -> PullRequest (dry_run)
  GitHubClient(token, repo, base, transport=)       # httpx, api.github.com
    open_pr(pr) -> url    # GET base ref sha, POST git/refs, PUT contents, POST pulls
service.decide(incident_id, decision, by, comment) -> IncidentRecord   # 404 / 409
```

Field path → patch: parse with `kiroween_shared.proposals.parse_path`. `adjust_resources`
paths become `spec.template.spec.containers[name=<container>].<field>`. Strategic merge
when every list step is `[name=…]`; otherwise JSON 6902 with `/` pointers (`~0`/`~1`
escaped), `test` current then `replace` proposed. A PVC `spec.storageClassName` change
carries a body warning that the field is immutable and the claim must be recreated.

The GitHub branch is `remediation/<incident-id>`; the commit message and PR title are
`fix(<namespace>): <field> on <kind>/<name> (incident <id>)`. The token is read from env
once at startup, never logged, never returned by the API.

Origin check: allowed when there is no `Origin` header (curl) or its host equals the
request's `Host` header.

## 6. UI

`src/kiroween_orchestrator/ui/`: `index.html`, `app.js`, `style.css`, `art.js` (the six
SVG characters as DOM-built elements). Mounted with `StaticFiles` at `/ui`, with a
middleware adding `Content-Security-Policy: default-src 'self'; img-src 'self' data:;
style-src 'self'; script-src 'self'; frame-ancestors 'none'`. `app.js` polls
`GET /incidents?limit=50` every 3 s and the selected incident every 3 s; it builds DOM with
`createElement`/`textContent` only. `GET /` stays the health-style default; `/ui/` is the
entry.

## 7. Chart

- `agents.specialists` gains `incident-memory:incidentMemory:kiroween_incident_memory:
  INCIDENT_MEMORY_URL`. It gets a Deployment (env `KNOWLEDGE_BASE_ID`, `TOP_K`,
  `MIN_SCORE`, no `MODEL_ID`), Service, SA, ingress from the orchestrator only, orchestrator
  egress, metrics and Pod Identity policies. No RBAC (it reads nothing in the cluster).
- Orchestrator env: `GITHUB_TOKEN` from Secret `orchestrator-github`/`token`
  (`optional: true`), `GITHUB_REPOSITORY`, `GITHUB_BASE_BRANCH`.
- `metrics.serviceMonitor.enabled: true`, with
  `argocd.argoproj.io/sync-options: SkipDryRunOnMissingResource=true`; the agents
  Application gets a sync retry so it converges once the CRD exists.
- `templates/dashboards.yaml`: ConfigMap `kiroween-dashboards` labelled
  `grafana_dashboard: "1"`, JSON from `dashboards/*.json` via `.Files.Get`.
- Watcher default and chart `excludedNamespaces` add `monitoring`.

## 8. Monitoring Application

Source `https://prometheus-community.github.io/helm-charts`, chart `kube-prometheus-stack`
92.21.2, values inline (same pattern as `metrics-server.yaml`; an OCI source would need a
repository registered in Argo CD). Sync options `CreateNamespace=true`, `ServerSideApply=true` (the CRDs exceed the
client-side annotation limit). Sizes: Prometheus 200m/1Gi request, 1 CPU/2Gi limit,
retention `2d`, `retentionSize: 3GB`, emptyDir 4Gi; Grafana 50m/128Mi; operator, kube-state-
metrics 25m/64Mi; node-exporter 20m/32Mi. `serviceMonitorSelectorNilUsesHelmValues:
false`, same for pod monitors and rules. Grafana sidecar `searchNamespace: ALL`,
`admin.existingSecret: grafana-admin`.

## Decisions

- incident-memory is a plain service over Retrieve, not a model-backed agent: the task
  is search, and a model call per incident would add cost and latency without changing
  the result. The specialist already reasons over the hits.
- The approval PR targets `gitops/remediations/`, because the chaos workloads are not
  managed by Argo CD. The PR is the audit trail of an approved change; applying it to a
  workload requires that workload's manifests to be in `/gitops`.
- No authentication: the UI and approval endpoints are reachable only through
  `kubectl port-forward`, so Kubernetes RBAC on `pods/portforward` is the access control.
  The Origin check stops other web pages from using that tunnel.
