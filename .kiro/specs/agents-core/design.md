# agents-core — Design

Implements `requirements.md` in this directory. Requirement IDs are cited as `REQ-…`.

## 1. Repository layout

```
agents/
├── shared/                      # library, no entrypoint
│   ├── pyproject.toml
│   ├── src/kiroween_shared/
│   │   ├── config.py            # env-driven settings, eu. prefix check
│   │   ├── bedrock.py           # make_model(), model-call guard hook
│   │   ├── metrics.py           # counters, price table, record_usage()
│   │   ├── context.py           # incident_id / agent contextvars
│   │   ├── models.py            # Incident, Classification, Diagnosis, Proposal, ...
│   │   ├── server.py            # app factory: /healthz /readyz, metrics port
│   │   └── k8s/
│   │       ├── client.py        # ReadOnlyKube: the only object that touches the API
│   │       ├── redact.py        # env/secret redaction, size bounds
│   │       └── tools.py         # Strands @tool functions over ReadOnlyKube
│   └── tests/
├── orchestrator/
│   ├── pyproject.toml, Dockerfile
│   ├── src/kiroween_orchestrator/
│   │   ├── api.py               # POST/GET /incidents
│   │   ├── service.py           # queue, workers, timeouts, store
│   │   ├── agent.py             # classify() and delegate() with Strands
│   │   ├── specialists.py       # @tool wrappers that call specialist services over HTTP
│   │   ├── store.py             # bounded in-memory incident store
│   │   └── watcher/             # second entrypoint, same image
│   │       ├── main.py, detect.py, dedupe.py, client.py
│   └── tests/
├── cpu_memory/
│   ├── pyproject.toml, Dockerfile
│   ├── src/kiroween_cpu_memory/
│   │   ├── api.py               # POST /investigate
│   │   ├── agent.py             # Strands agent, prompt, tool subset
│   │   └── validate.py          # proposal validation against the incident
│   └── tests/
gitops/
├── apps/{agents.yaml, metrics-server.yaml}
└── charts/agents/               # Chart.yaml, values.yaml, templates/, tests/
chaos/oom/{oom.sh, manifests.yaml}
.github/workflows/agents-images.yml
tools/set_image_tag.py           # edits the CI-managed tag lines in values.yaml
```

Python 3.12, one `pyproject.toml` per package (steering), locked with `uv`. The two agents depend on `shared` by path. Docker builds use the repository root as the context so `agents/shared` is copied in.

## 2. Runtime view

```
 kube-apiserver ──watch pods──▶ orchestrator-watcher ──POST /incidents──▶ orchestrator ──POST /investigate──▶ cpu-memory
   (orchestrator-watcher-sa:       (no AWS role)                           (Haiku, Pod Identity               (Sonnet, Pod Identity,
    pods get/list/watch)                                                    orchestrator-sa, no k8s RBAC)      cpu-memory-sa, k8s read-only)
                                                                                  │                                 │
                                                                                  └──── Bedrock Converse via eu.* inference profile ────┘
 Prometheus (not installed yet) ──scrape :9090/metrics──▶ every agent pod
```

Ports: API 8080, metrics 9090 (REQ-MET-05). Only the orchestrator calls a specialist (REQ-ORC-08).

## 3. Data models (`shared/models.py`, Pydantic v2)

| Model | Fields |
|---|---|
| `Incident` | `incident_id`, `source`, `reason`, `namespace`, `workload{kind,name}`, `pod`, `container`, `message`, `observed_at` (REQ-ORC-02) |
| `Classification` | `category` (`cpu_memory`/`network`/`deploy_rollout`/`storage`/`unknown`), `rationale` (one sentence) |
| `Evidence` | `tool`, `field`, `value` |
| `Diagnosis` | `root_cause`, `confidence` (`low`/`medium`/`high`), `evidence: list[Evidence]` |
| `Proposal` | `action` (`adjust_resources`/`investigate_further`/`none`), `target{namespace,kind,name,container}`, `field`, `current`, `proposed`, `rationale`, `risk`, `requires_approval: Literal[True]`, `delivery` (fixed text: PR against `/gitops` after approval) |
| `Investigation` | `diagnosis`, `proposal`, `usage` |
| `IncidentRecord` | `incident`, `status` (`accepted`/`processing`/`awaiting_approval`/`no_specialist`/`failed`/`completed`), `classification`, `investigation`, `usage_by_agent`, `error`, timestamps |

`requires_approval` is a `Literal[True]`, so a proposal that says otherwise cannot be constructed.

## 4. Shared library

### 4.1 Bedrock client (REQ-BED)
`make_model(agent)` reads the model ID from `<AGENT>_MODEL_ID`, rejects any value not starting with `eu.` at startup (REQ-BED-03), and returns `strands.models.BedrockModel(model_id=…, region_name="eu-west-1")`. Strands' Bedrock provider uses the Converse API. Credentials come from the default boto3 chain, which Pod Identity feeds (REQ-BED-04). A botocore retry config (`mode="adaptive"`, bounded attempts) covers throttling (REQ-BED-06). `AccessDeniedException` and `ResourceNotFoundException` are caught once and re-raised as `ModelUnavailable(model_id)` with no retry (REQ-BED-05).

Call cap (REQ-BED-07): a Strands hook on the before-model-call event counts calls per invocation and raises `CallBudgetExceeded` past `MAX_MODEL_CALLS` (default 15). `max_tokens` is set per call in the model config.

### 4.2 Metrics (REQ-MET)
Counters `agent_llm_input_tokens_total`, `agent_llm_output_tokens_total`, `agent_llm_cost_usd_total`, labels `agent` and `incident_id`. A fresh `Agent` is created for every invocation, so `result.metrics.accumulated_usage` (`inputTokens`, `outputTokens`, `totalTokens`) is that invocation's usage and no before/after delta is needed. `record_usage(agent, incident_id, model_id, usage)` is called once after each invocation. The price table is `PRICES_JSON` (USD per million tokens, input and output, by model ID) from chart values. An unpriced model records tokens only and warns once (REQ-MET-03).

`incident_id` comes from a `contextvars.ContextVar` set by the request handler, default `"none"` (REQ-MET-04). The metrics server is `prometheus_client.start_http_server(9090)` in a daemon thread, so the API port never serves `/metrics`.

Cardinality: one series set per incident. Fine for this slice; flagged in the requirements.

### 4.3 Read-only Kubernetes access (REQ-K8S)
`ReadOnlyKube` is the only class that imports the Kubernetes client. It exposes a fixed list of methods built on `read_namespaced_*`/`list_*` calls. The tools take it by injection, so tests pass a fake.

| Tool | API call |
|---|---|
| `get_pod` | `CoreV1.read_namespaced_pod` |
| `list_pods` | `CoreV1.list_namespaced_pod` (label selector) |
| `get_pod_logs` | `CoreV1.read_namespaced_pod_log` (`previous`, `tail_lines`, `limit_bytes`) |
| `list_events` | `CoreV1.list_namespaced_event` (field selector on the involved object) |
| `get_owner_chain` | pod `ownerReferences`, then `AppsV1.read_namespaced_replica_set` / workload |
| `get_quota_and_limits` | `CoreV1.list_namespaced_resource_quota`, `list_namespaced_limit_range` |
| `get_hpa` | `AutoscalingV2.list_namespaced_horizontal_pod_autoscaler` |
| `get_node` | `CoreV1.read_node` (conditions, allocatable) |
| `get_pod_metrics` | `CustomObjects.get_namespaced_custom_object("metrics.k8s.io", …)` |

Guard test (REQ-K8S-02): an AST scan of `k8s/` fails on any attribute call whose name starts with `create`, `patch`, `replace`, `delete`, `connect` or contains `exec`.

Redaction (REQ-K8S-03): `get_pod` returns containers with `env` reduced to `[{name}]` only, and never returns `envFrom` contents, volumes' secret data or `valueFrom` targets' values. Secrets are never requested. Size (REQ-K8S-04): every result passes through `bound(text, max_chars)`; logs use `tail_lines=200` default, capped at 1000. API errors become `{"error": "forbidden|not_found|…", "detail": "…"}` (REQ-K8S-05, K8S-06).

### 4.4 Server helper
`create_app(name, ready_check)` builds a FastAPI app with `/healthz` (process alive) and `/readyz` (config loaded; it never calls Bedrock, so probes are free), starts the metrics server, and installs JSON logging with `incident_id` from the context (REQ-ORC-14).

## 5. Orchestrator

### 5.1 API and processing (REQ-ORC-01 to 05, 11 to 13)
`POST /incidents` validates the body (422 on failure, no model call), checks the store for the ID (returns the existing record), enqueues, and returns 202. A bounded `asyncio.Queue` (default 20) feeds a fixed worker pool (default 2). A full queue returns 503. Each job runs under `asyncio.timeout(INCIDENT_TIMEOUT)` (300 s); delegation has its own 120 s HTTP timeout. The store is an `OrderedDict` capped at 500 (REQ-ORC-13).

### 5.2 Classification and delegation (REQ-ORC-06 to 10)
1. **Classify.** A Haiku agent with `structured_output_model=Classification` and no tools. The category and rationale go on the record.
2. **Delegate.** For `cpu_memory`, a second Haiku agent is given the specialist as a Strands tool (agents-as-tools):

   ```python
   @tool
   async def investigate_cpu_memory() -> str:
       """Ask the cpu-memory specialist to diagnose the incident under review."""
       # POST http://cpu-memory:8080/investigate with the incident the orchestrator
       # accepted, 120 s timeout. The parsed Investigation goes into a per-call sink and
       # a short summary is returned to the model.
   ```

   The tool takes no arguments. An earlier draft passed the incident as a JSON string
   argument, which would have let the model change what is forwarded.

   The tool stores the parsed `Investigation` in the incident context. The record is built from that stored object, not from the model's retelling of it, so a model that paraphrases the result cannot change the proposal.
3. **Guarantee the call.** After the agent returns, if the sink is empty the orchestrator calls the specialist once directly and records `delegation: "forced"`, so routing is deterministic even if the model skips the tool. If the specialist call failed inside the tool, the error is raised instead and the specialist is not called again (REQ-ORC-11).
4. **Other categories.** No specialist is registered, so the status is `no_specialist` (REQ-ORC-09).
5. **Finish.** Status `awaiting_approval` when a proposal exists; per-agent usage is summed from the orchestrator's own result and the specialist's returned usage (REQ-ORC-10).

The orchestrator has no Kubernetes RBAC and does not import the Kubernetes client.

## 6. Event watcher (REQ-WAT)

Same image as the orchestrator, entrypoint `python -m kiroween_orchestrator.watcher`. It uses `kubernetes.watch.Watch().stream(list_pod_for_all_namespaces)`.

- **Detection (`detect.py`).** For each container status, an incident is raised when `lastState.terminated.reason` or `state.terminated.reason` is in `WATCH_REASONS` (default `OOMKilled`). `incident_id` is the first 16 hex characters of a SHA-256 over namespace, workload, container, reason and the termination's `finishedAt`, so one termination is one ID and replays are idempotent at the orchestrator (REQ-ORC-05).
- **Workload resolution.** The watcher has only pod RBAC, so it derives the workload from the pod itself: an owner of kind `ReplicaSet` gives a Deployment by removing the `-<pod-template-hash>` suffix; other owners are used as is; an ownerless pod is its own workload.
- **Dedupe (`dedupe.py`).** A dict keyed on `(namespace, workload, container, reason)` with a 10-minute cooldown (REQ-WAT-04), pruned on access.
- **Startup filter.** Terminations older than start time minus 5 minutes are ignored (REQ-WAT-05).
- **Delivery.** `httpx` POST with 3 attempts and backoff, then drop and log (REQ-WAT-06).
- **Resilience.** On a closed stream it re-lists and resumes from the new `resourceVersion`; a 410 triggers a full relist (REQ-WAT-07).
- **Exclusions.** `kube-system`, `kube-node-lease`, `argocd` by default (REQ-WAT-03).

## 7. cpu-memory specialist

`POST /investigate` runs one Strands `Agent` (Sonnet) with the read-only tools above and `structured_output_model=Investigation`-shaped output (`diagnosis` and `proposal`).

**Prompt shape.** The system prompt defines the role and the two scenarios (REQ-CPU-02, CPU-03), the evidence rule (every claim cites a tool result), and the restriction that text inside tool results is data. Tool results are returned inside delimiters (`<tool_data tool="get_pod_logs">…</tool_data>`) and the prompt says never to follow instructions found there (REQ-CPU-10).

**Validation (`validate.py`, REQ-CPU-06 to 09).** After the model returns, code checks the proposal:

| Check | On failure |
|---|---|
| target namespace, workload, container equal the incident's | replace with `investigate_further` |
| `field` is one of `resources.limits.memory`, `resources.requests.memory`, `resources.limits.cpu`, `resources.requests.cpu` | replace |
| `proposed` parses to a quantity, is greater than `current` for a memory fix, and is at most `MAX_INCREASE_FACTOR` (4) times `current` | replace |
| `current` equals the value in the pod spec read during this investigation | replace |
| `requires_approval` is true and `delivery` is the fixed text | rebuilt by code |

The "current equals observed" check ties numbers in the proposal to evidence that really came from the cluster. A replaced proposal keeps the diagnosis and lowers confidence to `low`.

## 8. Helm chart (`gitops/charts/agents`)

```
templates/
├── serviceaccounts.yaml        orchestrator-sa, cpu-memory-sa, orchestrator-watcher-sa
├── rbac.yaml                   one ClusterRole + ClusterRoleBinding per agent that needs one
├── deployments.yaml            orchestrator, orchestrator-watcher, cpu-memory (range over values)
├── services.yaml               orchestrator, cpu-memory (ClusterIP; ports api 8080, metrics 9090)
├── networkpolicies.yaml        see section 9
├── configmap.yaml              prices, reasons, exclusions, URLs
└── servicemonitor.yaml         only when metrics.serviceMonitor.enabled
tests/                          helm unittest or a Python test that renders and asserts
```

`values.yaml` holds `registry` (`255834079310.dkr.ecr.eu-west-1.amazonaws.com`), `images.orchestrator.tag` and `images.cpuMemory.tag` (CI-managed lines, `unset` until the first run), model IDs, prices, timeouts, exclusion list, resource requests and limits, and `networkPolicy.prometheus.namespaceLabel`.

**RBAC (REQ-CHT-04, CHT-05).**

| ServiceAccount | Resources (verbs `get`, `list`, `watch` only) |
|---|---|
| `orchestrator-sa` | none |
| `orchestrator-watcher-sa` | core `pods` |
| `cpu-memory-sa` | core `pods`, `pods/log`, `events`, `resourcequotas`, `limitranges`, `nodes`; `apps` `deployments`, `replicasets`, `statefulsets`, `daemonsets`; `autoscaling` `horizontalpodautoscalers`; `metrics.k8s.io` `pods`, `nodes` |

The chart test renders every Role and ClusterRole and fails on any other verb, any `*`, or `secrets`.

**Pod spec (REQ-CHT-06).** `runAsNonRoot`, `runAsUser: 10001`, `readOnlyRootFilesystem: true` with an `emptyDir` on `/tmp`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`, `allowPrivilegeEscalation: false`, requests and limits, liveness `/healthz`, readiness `/readyz`. No `AWS_*` variables (REQ-CHT-03).

**Argo CD.** `gitops/apps/agents.yaml` is an `Application` with `CreateNamespace=true`, automated `prune` and `selfHeal`, source `gitops/charts/agents` on `main`. The root app already syncs `gitops/apps`, so adding the file creates the child.

## 9. Network policies (REQ-NET)

Policies are named by purpose and combine additively:

| Policy | Selects | Effect |
|---|---|---|
| `default-deny` | all pods | deny all ingress and egress |
| `allow-watcher-to-orchestrator` | `orchestrator` | ingress 8080 from `orchestrator-watcher` |
| `allow-orchestrator-to-cpu-memory` | `cpu-memory` | ingress 8080 from `orchestrator` |
| `allow-metrics-scrape` | all agent pods | ingress 9090 from the Prometheus namespace label |
| `allow-dns` | all pods | egress TCP and UDP 53 to `k8s-app=kube-dns` in `kube-system` |
| `allow-https` | all pods | egress TCP 443 to `0.0.0.0/0` |
| `allow-pod-identity-agent` | `orchestrator`, `cpu-memory` | egress TCP 80 to `169.254.170.23/32` (REQ-NET-09) |
| `allow-watcher-egress` | `orchestrator-watcher` | egress 8080 to `orchestrator` pods |
| `allow-orchestrator-egress` | `orchestrator` | egress 8080 to `cpu-memory` pods |

The Pod Identity Agent rule was missing from the first draft of this table; without it the agents could not fetch credentials once default-deny egress applied. Kubelet probe traffic comes from the node and is not blocked by pod policies. The first deploy confirms this: if a pod fails readiness only once policies exist, that assumption was wrong. The negative tests in REQ-E2E-07 run a pod in `default` with `nc -z -w 3`.

## 10. Metrics server

`gitops/apps/metrics-server.yaml`: `Application` with `repoURL: https://kubernetes-sigs.github.io/metrics-server/`, `chart: metrics-server`, `targetRevision: 3.14.0`, `destination.namespace: kube-system`, helm values `replicas: 1` and small requests and limits. Acceptance is REQ-MSV-03.

## 11. Chaos scenario (`chaos/oom`)

`oom.sh up|down`. Before anything else it reads `kubectl config current-context` and aborts unless it ends in `cluster/${EXPECTED_CLUSTER:-kiroween-prod}` (REQ-CHO-03). `manifests.yaml` holds the Namespace `chaos-oom` and a Deployment `oom-demo` with `public.ecr.aws/docker/library/python:3.12-slim`, memory request 32Mi and limit 64Mi, running a loop that appends 8 MiB chunks to a list until the kernel kills it. `down` runs `kubectl delete namespace chaos-oom --ignore-not-found`. The script applies and deletes only that namespace (REQ-CHO-04).

## 12. CI/CD (`.github/workflows/agents-images.yml`)

| Job | Runs on | Permissions | Steps |
|---|---|---|---|
| `test` | PR, push, dispatch | `contents: read` | `uv sync`, `ruff check`, `pytest` per package, chart render tests |
| `build` | PR (no push), `main` (push) | `contents: read`; plus `id-token: write` on `main` | matrix `[orchestrator, cpu_memory]`; buildx for `linux/amd64`; on `main`: assume the ECR push role, log in, skip if the SHA tag exists (`ecr:DescribeImages`), push |
| `update-chart` | `main`, after every `build` leg succeeded | `contents: write` | `tools/set_image_tag.py` edits the two CI-managed lines; commit `chore(deploy): <sha> [skip ci]` as the Actions bot; on a rejected push, `git pull --rebase` and retry up to 3 times |

Trigger paths are `agents/**` and the workflow file; `gitops/**` is excluded so the tag commit cannot loop (REQ-CI-14). Concurrency group `main` with `cancel-in-progress: false` (REQ-CI-09). Actions are pinned to full commit SHAs (REQ-CI-08). The role ARN is read from the repository variable `ECR_PUSH_ROLE_ARN`. The workflow uses no `environment:` (REQ-CI-10).

`set_image_tag.py` rewrites lines of the form `tag: <value> # ci-managed:orchestrator`, so it never parses and re-serialises the whole YAML file and cannot reorder or reformat the rest of `values.yaml`.

## 13. Testing (REQ-PKG)

Unit tests inject fakes at three seams: the Strands `Agent` factory, `ReadOnlyKube`, and the HTTP client. They cover every item in REQ-PKG-03. The pieces that need the real SDK (tool registration, `structured_output_model`, `accumulated_usage` keys) get one contract test each that builds a real `Agent` with a stub model object and never calls Bedrock.

## 14. Traceability

| Requirement area | Where |
|---|---|
| REQ-BED, REQ-MET, REQ-K8S | `shared` §4 |
| REQ-ORC | orchestrator §5 |
| REQ-WAT | watcher §6 |
| REQ-CPU | cpu-memory §7 |
| REQ-CHT, REQ-NET | chart §8, §9 |
| REQ-MSV | §10 |
| REQ-CHO | §11 |
| REQ-CI | §12 |
| REQ-PKG | §13 |
| REQ-E2E | tasks.md, final tasks |

## 15. Decisions and unverified points

- **Fresh agent per invocation** keeps token accounting simple and avoids carrying one incident's conversation into the next. It costs a little setup time per call.
- **Forced delegation fallback** (§5.2 step 3) trades a possible second call for a guaranteed route.
- **`accumulated_usage` keys.** The Strands docs confirm `totalTokens` and the optional cache keys. `inputTokens` and `outputTokens` follow the Bedrock `Usage` shape and are confirmed by the first contract test.
- **Strands version.** Pinned at implementation time by `uv lock`; the structured output API (`structured_output_model`, `AgentResult.structured_output`) and hooks come from the current documentation, not a pinned install.
- **Probes under default-deny** and **metrics-server on Kubernetes 1.37** are assumptions the first deploy tests.
- **ECR push role** needs `ecr:DescribeImages` (`infra` TASK-19) before a workflow re-run is safe.
