# agents-core — Requirements

> EARS format. Each requirement follows one of:
> - **Ubiquitous**: The \<system\> shall \<action\>.
> - **Event-driven**: When \<trigger\>, the \<system\> shall \<action\>.
> - **State-driven**: While \<state\>, the \<system\> shall \<action\>.
> - **Option**: Where \<feature is included\>, the \<system\> shall \<action\>.
> - **Unwanted**: If \<condition\>, the \<system\> shall \<action\>.

## Scope

A vertical slice that takes one incident type from the cluster to a proposed fix:
an out-of-memory kill is detected, classified by the orchestrator, diagnosed by the
cpu-memory specialist, and returned as a proposal that a human must approve. It
runs on the EKS cluster built by the `infra` spec.

Components: `agents/shared`, `agents/orchestrator` (API and watcher),
`agents/cpu_memory`, `gitops/charts/agents`, `gitops/apps/agents.yaml`,
`gitops/apps/metrics-server.yaml`, `chaos/oom`, and a GitHub Actions image workflow
that also commits the new image tag to the chart.

Depends on `infra` tasks that are already applied: TASK-16 (the ECR push role the
workflow assumes), TASK-17 (NetworkPolicy enforcement) and TASK-18 (immutable image
tags). One more is needed for REQ-CI-16: the push role must also be allowed
`ecr:DescribeImages` on `kiroween/*` (`infra` TASK-19, not yet written).

Out of scope: see the last section.

## Model selection (approved)

Checked on 17 Nov 2026 with `aws bedrock list-inference-profiles` and a real
tool-use `Converse` call per profile, from `eu-west-1`.

| Role | Default model (EU inference profile) | Basis |
|---|---|---|
| Orchestrator | `eu.anthropic.claude-haiku-4-5-20251001-v1:0` | Classifies and delegates in short calls, so it needs speed and low cost. Returned a correct tool call in 567 ms. |
| cpu-memory specialist | `eu.anthropic.claude-sonnet-4-6` | Multi-step diagnosis over several tool results. Returned a correct tool call in 772 ms. |

- Approved on 17 Nov 2026. Both are configuration values (REQ-BED-02), so changing a
  model is a chart values change.
- All profiles route inside the EU: `eu-central-1`, `eu-north-1`, `eu-south-1`,
  `eu-south-2`, `eu-west-1`, `eu-west-3`. The Pod Identity roles from the `infra`
  spec already allow `inference-profile/eu.*` and `eu-*` foundation models.
- The newer profiles (`claude-haiku-5-5`, `claude-sonnet-5`, `claude-sonnet-5-5`,
  `claude-opus-4-7` and up) are listed as `ACTIVE`, and
  `get-foundation-model-availability` reports `AUTHORIZED`, but `Converse` fails
  with `AccessDeniedException` ("not available for this account"). The
  availability API is therefore not a reliable signal. Moving to a newer model
  needs account access first, then a configuration change only.
- Prices are configuration (REQ-MET-02), not code. I have not confirmed
  per-token prices for these two models from AWS; confirm them before relying on
  the cost metric.

---

## 1. Shared Bedrock client

**REQ-BED-01 (Ubiquitous)**
`agents/shared` shall provide a function that returns a Strands Agents `BedrockModel` configured for region `eu-west-1`, which calls the Bedrock Converse API.

**REQ-BED-02 (Ubiquitous)**
The model identifier shall be read from configuration per agent, with the defaults in the model selection table, and shall be an EU cross-region inference profile ID.

**REQ-BED-03 (Unwanted)**
If a configured model identifier does not start with `eu.`, the agent shall refuse to start and log the offending value, because the agents' IAM permissions cover only EU inference profiles.

**REQ-BED-04 (Ubiquitous)**
The client shall obtain AWS credentials only from the default credential chain (EKS Pod Identity) and shall not read or accept static credentials.

**REQ-BED-05 (Unwanted)**
If Bedrock returns `AccessDeniedException` or `ResourceNotFoundException` for the model, the agent shall fail that invocation immediately with an error that names the model identifier, and shall not retry.

**REQ-BED-06 (Unwanted)**
If Bedrock returns a throttling error, the client shall retry with exponential backoff up to a configured maximum number of attempts, then fail the invocation.

**REQ-BED-07 (Ubiquitous)**
Each agent shall enforce a maximum number of model calls per invocation (default 15) and a maximum output token count per call, so a looping agent cannot spend without bound.

## 2. Token and cost metrics

**REQ-MET-01 (Event-driven)**
When a Bedrock response is received, `agents/shared` shall add its input and output token counts to the Prometheus counters `agent_llm_input_tokens_total` and `agent_llm_output_tokens_total`, each labelled `agent` and `incident_id`.

**REQ-MET-02 (Event-driven)**
When a Bedrock response is received for a model that has an entry in the configured price table (USD per million input and output tokens), `agents/shared` shall add the computed cost to the counter `agent_llm_cost_usd_total`, labelled `agent` and `incident_id`.

**REQ-MET-03 (Unwanted)**
If a model has no entry in the price table, `agents/shared` shall still record the token counters, shall not increment the cost counter, and shall log a warning once per model.

**REQ-MET-04 (Ubiquitous)**
Usage shall be attributed to the agent that made the model call and to the incident being handled. A call made outside an incident shall use `incident_id="none"`.

**REQ-MET-05 (Ubiquitous)**
Each agent shall serve `/metrics` in Prometheus text format on a dedicated metrics port (default 9090), separate from the API port (default 8080), so allowing a scraper to reach the metrics port does not expose the API.

**REQ-MET-06 (Option)**
Where `metrics.serviceMonitor.enabled` is true in the chart values, the chart shall create a `ServiceMonitor` for each agent. The default shall be false, because the cluster has no Prometheus Operator CRDs.

## 3. Read-only Kubernetes tools

**REQ-K8S-01 (Ubiquitous)**
`agents/shared` shall provide Strands tools for at least: getting a pod, listing pods by namespace and label selector, reading container logs (including the previous container), listing events for an object, resolving a pod to its owning workload, reading resource quotas and limit ranges, reading a HorizontalPodAutoscaler, and reading node conditions and allocatable resources.

**REQ-K8S-02 (Ubiquitous)**
The tools shall use only the Kubernetes `get`, `list` and `watch` verbs. A unit test shall fail if the tools module calls any create, patch, replace, delete or exec method of the Kubernetes client.

**REQ-K8S-03 (Ubiquitous)**
The tools shall not read Secrets, and shall return environment variables by name only, never by value, including values resolved from a Secret or ConfigMap reference.

**REQ-K8S-04 (Ubiquitous)**
Each tool result shall be bounded in size (log tail default 200 lines, hard limit 1000 lines, and a maximum character count per result) so one call cannot exhaust the model's context or token budget.

**REQ-K8S-05 (Unwanted)**
If the Kubernetes API returns 403 or 404, or the object no longer exists, the tool shall return a structured error result to the agent and shall not raise, so the agent can continue and report the gap.

**REQ-K8S-06 (Unwanted)**
If the `metrics.k8s.io` API is not available (for example while metrics-server is starting), the pod metrics tool shall return a result that states metrics are unavailable, and the investigation shall continue without it.

## 4. Orchestrator

**REQ-ORC-01 (Ubiquitous)**
The orchestrator shall run an HTTP server with `POST /incidents`, `GET /incidents/{id}`, `GET /healthz`, `GET /readyz` and `GET /metrics`.

**REQ-ORC-02 (Ubiquitous)**
An incident submitted to `POST /incidents` shall contain: `incident_id`, `source`, `reason`, `namespace`, `workload` (kind and name), `pod`, `container`, `message` and `observed_at`.

**REQ-ORC-03 (Event-driven)**
When a valid incident is received, the orchestrator shall respond `202 Accepted` with the `incident_id` and process the incident asynchronously.

**REQ-ORC-04 (Unwanted)**
If an incident is missing required fields or has invalid values, the orchestrator shall respond `422` and shall make no model call.

**REQ-ORC-05 (Event-driven)**
When an incident arrives with an `incident_id` that was already accepted, the orchestrator shall return the existing record and shall not process it again.

**REQ-ORC-06 (Event-driven)**
When an incident is processed, the orchestrator agent shall classify it into exactly one of `cpu_memory`, `network`, `deploy_rollout`, `storage` or `unknown`, using structured output, and shall record the category with a one-sentence rationale.

**REQ-ORC-07 (Event-driven)**
When the category is `cpu_memory`, the orchestrator shall delegate to the cpu-memory specialist through a Strands tool (agents-as-tools pattern). The tool shall call the cpu-memory service over cluster-internal HTTP and shall not import or run the specialist in the orchestrator's process.

**REQ-ORC-08 (Ubiquitous)**
Only the orchestrator shall call a specialist. Specialists shall not call each other or the orchestrator.

**REQ-ORC-09 (Event-driven)**
When the category has no deployed specialist, the orchestrator shall record the status `no_specialist` with the category, shall make no delegation, and shall complete without error.

**REQ-ORC-10 (Event-driven)**
When the specialist returns, the orchestrator shall produce a final incident record containing the classification, the specialist's diagnosis and proposal, the per-agent token and cost totals, and the status `awaiting_approval` if a proposal was made.

**REQ-ORC-11 (Unwanted)**
If delegation does not complete within the configured timeout (default 120 seconds), or the whole incident exceeds its limit (default 300 seconds), the orchestrator shall set the status `failed` with the reason and shall not retry automatically.

**REQ-ORC-12 (Ubiquitous)**
The orchestrator shall limit concurrent incident processing (default 2) and bound its pending queue. While the queue is full, it shall respond `503` to new incidents.

**REQ-ORC-13 (Ubiquitous)**
The orchestrator shall keep incident records in memory with a bounded size (default 500, oldest evicted first). Records are lost on restart; persistence is out of scope.

**REQ-ORC-14 (Ubiquitous)**
The orchestrator shall write structured JSON logs that include `incident_id` and shall not log container log content or environment values.

## 5. Event watcher

**REQ-WAT-01 (Ubiquitous)**
The watcher shall run as a separate Deployment from the orchestrator API, using the orchestrator image with a different entrypoint.

**REQ-WAT-02 (Event-driven)**
When a container in a watched namespace terminates with a reason in the configured list (default `OOMKilled`), the watcher shall create an incident and `POST` it to the orchestrator.

**REQ-WAT-03 (Ubiquitous)**
The watcher shall watch pods in all namespaces except a configured exclusion list (default `kube-system`, `kube-node-lease`, `argocd`).

**REQ-WAT-04 (Ubiquitous)**
The watcher shall suppress repeat incidents with the same namespace, owning workload, container and reason within a cooldown window (default 10 minutes). The key shall use the workload, not the pod name, because a restarted workload gets new pod names.

**REQ-WAT-05 (Event-driven)**
When the watcher starts, it shall ignore terminations that finished more than a configured period before startup (default 5 minutes), so a restart does not replay history.

**REQ-WAT-06 (Unwanted)**
If the orchestrator is unreachable, the watcher shall retry with backoff up to a limit, then log and drop the incident. It shall not queue without bound.

**REQ-WAT-07 (Unwanted)**
If the watch connection drops, the watcher shall reconnect, re-list pods, and continue without exiting.

**REQ-WAT-08 (Ubiquitous)**
The watcher shall have no Bedrock permissions and no Pod Identity association.

**REQ-WAT-09 (Ubiquitous)**
In this slice the watcher shall detect only the OOMKilled reason. CPU throttling has no pod event, so throttling incidents reach the orchestrator only through `POST /incidents`.

## 6. cpu-memory specialist

**REQ-CPU-01 (Ubiquitous)**
The specialist shall run an HTTP server with `POST /investigate`, `GET /healthz`, `GET /readyz` and `GET /metrics`, and shall be reachable only through the `cpu-memory` Service inside the cluster.

**REQ-CPU-02 (Event-driven)**
When it receives an `OOMKilled` incident, the specialist shall gather, using the shared tools: the container's resource requests and limits, the last terminated state (reason, exit code), restart count, the previous container's log tail, recent events, the owning workload, and any applicable resource quota or limit range.

**REQ-CPU-03 (Event-driven)**
When it receives a CPU throttling incident, the specialist shall gather the container's CPU requests and limits, any HorizontalPodAutoscaler for the workload, and pod CPU metrics where available.

**REQ-CPU-04 (Ubiquitous)**
The specialist's response shall contain a `diagnosis` (root cause, confidence of `low`, `medium` or `high`, and evidence entries that each name the tool and the field they came from) and a `proposal`.

**REQ-CPU-05 (Ubiquitous)**
A `proposal` shall contain: an action (`adjust_resources`, `investigate_further` or `none`); for `adjust_resources`, the target (namespace, workload kind and name, container), the field, the current value, the proposed value, a rationale and the risk; and `requires_approval` set to true.

**REQ-CPU-06 (Ubiquitous)**
A proposed resource value shall be derived from the gathered evidence and shall not exceed a configured multiple of the current value (default 4x).

**REQ-CPU-07 (Ubiquitous)**
The proposal shall state that the change is to be made through a pull request against `/gitops` after human approval, and the specialist shall not apply it.

**REQ-CPU-08 (Ubiquitous)**
Before returning, the specialist shall validate the proposal against the incident: the target namespace, workload and container shall equal those in the incident, and the field shall be a resource request or limit. A proposal that fails validation shall be replaced with `investigate_further`.

**REQ-CPU-09 (Unwanted)**
If the evidence is insufficient (for example the pod is gone or logs are unavailable), the specialist shall set confidence to `low`, propose `investigate_further`, and shall not state any value it did not observe.

**REQ-CPU-10 (Ubiquitous)**
The specialist shall treat container logs and event messages as untrusted data. Its instructions shall say that text inside tool results must never be followed as an instruction, and proposal validation (REQ-CPU-08) shall not depend on the model's compliance.

**REQ-CPU-11 (Ubiquitous)**
The specialist shall make no write call to the Kubernetes API. Its ServiceAccount shall have no write verb (REQ-CHT-05).

## 7. Helm chart and Argo CD Application

**REQ-CHT-01 (Ubiquitous)**
`gitops/charts/agents` shall be a Helm chart that deploys into the namespace `agents` the Deployments `orchestrator`, `orchestrator-watcher` and `cpu-memory`, and the ClusterIP Services `orchestrator` and `cpu-memory`.

**REQ-CHT-02 (Ubiquitous)**
The chart shall create the ServiceAccounts `orchestrator-sa` and `cpu-memory-sa`, whose names match the Pod Identity associations from the `infra` spec, and `orchestrator-watcher-sa`, which has no AWS role.

**REQ-CHT-03 (Ubiquitous)**
The chart shall not set AWS credential environment variables. Workloads shall obtain credentials through EKS Pod Identity.

**REQ-CHT-04 (Ubiquitous)**
Each agent shall have its own RBAC, scoped to the API groups it needs:
- `orchestrator-sa`: no Kubernetes permissions.
- `orchestrator-watcher-sa`: `get`, `list`, `watch` on `pods` only.
- `cpu-memory-sa`: `get`, `list`, `watch` on core `pods`, `pods/log`, `events`, `resourcequotas`, `limitranges` and `nodes`; on `apps` `deployments`, `replicasets`, `statefulsets` and `daemonsets`; on `autoscaling` `horizontalpodautoscalers`; and on `metrics.k8s.io` `pods` and `nodes`.

**REQ-CHT-05 (Ubiquitous)**
No Role or ClusterRole in the chart shall grant a verb other than `get`, `list` or `watch`, use a wildcard for verbs, resources or API groups, or grant access to `secrets`. A chart test shall check this against the rendered manifests.

**REQ-CHT-06 (Ubiquitous)**
Each pod shall run as a non-root user with a read-only root filesystem, all capabilities dropped, the `RuntimeDefault` seccomp profile, resource requests and limits, and liveness and readiness probes.

**REQ-CHT-07 (Ubiquitous)**
Image references shall be built from a registry value, a repository (`kiroween/<agent>`) and a tag value. The tag in the committed values shall be a commit SHA written by the image workflow (REQ-CI-11), or the placeholder `unset` before the first workflow run. It shall never be `latest`.

**REQ-CHT-08 (Ubiquitous)**
Model identifiers, region, cooldown and timeout settings, the namespace exclusion list, the price table and service URLs shall be set through chart values.

**REQ-CHT-09 (Ubiquitous)**
`helm lint` and `helm template` shall succeed for the chart with default values.

**REQ-CHT-10 (Ubiquitous)**
`gitops/apps/agents.yaml` shall be an Argo CD `Application` named `agents` in the `argocd` namespace. It shall source `gitops/charts/agents` from the `main` branch of this repository, deploy to the namespace `agents` with `CreateNamespace=true`, and use automated sync with `prune` and `selfHeal`.

## 8. Metrics server

**REQ-MSV-01 (Ubiquitous)**
`gitops/apps/metrics-server.yaml` shall be an Argo CD `Application` named `metrics-server` in the `argocd` namespace. It shall deploy the `metrics-server` Helm chart from `https://kubernetes-sigs.github.io/metrics-server/` into the namespace `kube-system`, with automated sync, `prune` and `selfHeal`.

**REQ-MSV-02 (Ubiquitous)**
The chart version shall be pinned to one exact version (3.14.0, application 0.9.0, the newest in the chart index on 17 Nov 2026), not a range.

**REQ-MSV-03 (Event-driven)**
When the Application has synced, the `v1beta1.metrics.k8s.io` APIService shall be `Available` and `kubectl top nodes` shall return values for both nodes. The chart declares no Kubernetes version constraint, so this check is the proof it works on Kubernetes 1.37.

**REQ-MSV-04 (Ubiquitous)**
The Application shall run a single replica with resource requests and limits, which is enough for a two-node cluster.

## 9. Network policies

`infra` TASK-17 turned on NetworkPolicy enforcement for the cluster, so these policies take effect.

**REQ-NET-01 (Ubiquitous)**
The chart shall create a default-deny `NetworkPolicy` in the namespace `agents` that selects all pods and denies all ingress and all egress. Every allowance below shall be an additional policy.

**REQ-NET-02 (Ubiquitous)**
The chart shall allow ingress to the `orchestrator` pods on the API port only from the `orchestrator-watcher` pods.

**REQ-NET-03 (Ubiquitous)**
The chart shall allow ingress to the `cpu-memory` pods on the API port only from the `orchestrator` pods.

**REQ-NET-04 (Ubiquitous)**
The chart shall allow ingress to the metrics port of every agent pod from pods in the namespace selected by the value `networkPolicy.prometheus.namespaceLabel` (default `kubernetes.io/metadata.name: monitoring`), and shall not allow that source to reach the API port.

**REQ-NET-05 (Ubiquitous)**
The chart shall allow egress from every agent pod to the cluster DNS pods (`k8s-app: kube-dns` in `kube-system`) on TCP and UDP port 53.

**REQ-NET-06 (Ubiquitous)**
The chart shall allow egress from every agent pod to TCP port 443 on any address, which covers Amazon Bedrock, AWS STS and the EKS API endpoint.

**REQ-NET-07 (Ubiquitous)**
The chart shall allow egress on the API port from the `orchestrator-watcher` pods to the `orchestrator` pods, and from the `orchestrator` pods to the `cpu-memory` pods, and no other pod-to-pod egress.

**REQ-NET-09 (Ubiquitous)**
The chart shall allow egress from the `orchestrator` and `cpu-memory` pods to the EKS Pod Identity Agent (`169.254.170.23/32`, TCP 80), from which they obtain AWS credentials. Without it, default-deny egress could block credential retrieval and every Bedrock call would fail.

**REQ-NET-08 (Unwanted)**
If a rendered policy selects pods outside the namespace `agents`, or a policy allows ingress from a source other than the ones listed above, a chart test shall fail.

## 10. Chaos scenario

**REQ-CHO-01 (Ubiquitous)**
`chaos/oom` shall contain a script with `up` and `down` commands and the Kubernetes manifests it applies.

**REQ-CHO-02 (Event-driven)**
When run with `up`, the script shall create the namespace `chaos-oom` and a Deployment named `oom-demo` whose container allocates memory beyond its 64Mi limit, so the container is repeatedly OOMKilled. The image shall come from a public registry that does not rate-limit anonymous pulls (public ECR), not Docker Hub.

**REQ-CHO-03 (Unwanted)**
If the current kubectl context does not target the expected cluster (default `kiroween-prod`, configurable), the script shall abort before changing anything.

**REQ-CHO-04 (Ubiquitous)**
The script shall act only on the namespace `chaos-oom`.

**REQ-CHO-05 (Event-driven)**
When run with `down`, the script shall delete the namespace `chaos-oom`, and shall succeed if it does not exist.

**REQ-CHO-06 (Ubiquitous)**
The watcher's namespace exclusion list shall not contain `chaos-oom`.

## 11. Image build and push

**REQ-CI-01 (Ubiquitous)**
A GitHub Actions workflow shall build the images for `orchestrator` and `cpu_memory`. The workflow shall map the package `cpu_memory` to the ECR repository `kiroween/cpu-memory`.

**REQ-CI-02 (Event-driven)**
When a commit is pushed to `main` that changes files under `agents/` or the workflow, or the workflow is started manually from `main`, the workflow shall build and push the images. The steps that use AWS or write to the repository shall run only on `main`.

**REQ-CI-03 (Event-driven)**
When a pull request changes those files, the workflow shall run the tests and build the images without pushing, and shall not assume any AWS role.

**REQ-CI-04 (Ubiquitous)**
The workflow shall run lint and unit tests before building, and shall not push if they fail.

**REQ-CI-05 (Ubiquitous)**
The workflow shall authenticate with GitHub OIDC by assuming the `kiroween-prod-gha-ecr-push` role (`infra` REQ-GHA-08), read from a repository variable and not hardcoded. The job that pushes images shall request only `id-token: write` and `contents: read`. The workflow shall use no long-lived AWS keys and shall not assume the Terraform apply role.

**REQ-CI-06 (Ubiquitous)**
The workflow shall tag each image with the full commit SHA, shall not tag `latest`, and shall build for `linux/amd64`.

**REQ-CI-07 (Ubiquitous)**
Each agent shall have a Dockerfile at `agents/<name>/Dockerfile`. The build context shall be the repository root so `agents/shared` is available, and the image shall run as a non-root user.

**REQ-CI-08 (Ubiquitous)**
Third-party actions in the workflow shall be pinned to a full commit SHA.

**REQ-CI-09 (Ubiquitous)**
The workflow shall use one concurrency group for `main` that queues runs and does not cancel a run in progress, so two runs never push or commit at the same time.

**REQ-CI-10 (Ubiquitous)**
The workflow shall not use a GitHub `environment`, because that changes the OIDC subject and would no longer match the ECR push role's trust policy.

**REQ-CI-11 (Event-driven)**
When the images for a commit on `main` have all been pushed, a separate `update-chart` job shall set the image tag for `orchestrator` and `cpu-memory` in `gitops/charts/agents/values.yaml` to that commit's SHA and commit the change to `main`. The commit message shall contain `[skip ci]`. Argo CD then deploys the change (REQ-CHT-10), and nobody edits the tag by hand.

**REQ-CI-12 (Ubiquitous)**
The `update-chart` job shall have no AWS credentials and shall request only `contents: write`. The commit shall be authored by the GitHub Actions bot and shall change only the image tag lines.

**REQ-CI-13 (Unwanted)**
If any image fails to build, test or push, the workflow shall not commit a new tag.

**REQ-CI-14 (Ubiquitous)**
The workflow's path filter shall not include `gitops/**`, so the tag commit cannot start another run. If the tag in the values file already equals the commit SHA, the job shall make no commit.

**REQ-CI-15 (Unwanted)**
If pushing the tag commit is rejected because `main` has moved, the job shall rebase and retry up to 3 times. If it still fails, the job shall fail and shall not force-push.

**REQ-CI-16 (Event-driven)**
When the image tag for a commit already exists in its ECR repository (a re-run of the workflow), the workflow shall skip pushing that image and treat it as pushed, because the repositories are immutable (`infra` REQ-ECR-05) and a second push would fail. The check uses `ecr:DescribeImages`, which `infra` TASK-19 adds to the push role.

## 12. Packaging and tests

**REQ-PKG-01 (Ubiquitous)**
`agents/shared`, `agents/orchestrator` and `agents/cpu_memory` shall each have their own `pyproject.toml` for Python 3.12, with dependency versions fixed by a lock file. The two agents shall depend on `agents/shared`.

**REQ-PKG-02 (Ubiquitous)**
Unit tests shall not call AWS or a Kubernetes cluster. They shall use a fake Bedrock client and fake Kubernetes responses.

**REQ-PKG-03 (Ubiquitous)**
Unit tests shall cover at least: the `eu.` prefix check, metric labels and values, cost calculation including an unpriced model, the read-only tool guard, secret and environment redaction, watcher deduplication and startup filtering, classification routing including `no_specialist`, and proposal validation including a proposal for the wrong workload.

## 13. End-to-end acceptance

**REQ-E2E-01 (Event-driven)**
When `chaos/oom up` is run on the deployed cluster, an incident for `chaos-oom/oom-demo` shall appear in the orchestrator within 5 minutes with the category `cpu_memory`.

**REQ-E2E-02 (Ubiquitous)**
For that incident, the final record shall contain a diagnosis that cites the `OOMKilled` reason and the 64Mi limit as evidence, and a proposal to raise the memory limit with `requires_approval` true, and the status `awaiting_approval`.

**REQ-E2E-03 (Ubiquitous)**
The token and cost counters for that incident shall be non-zero for both `orchestrator` and `cpu-memory`.

**REQ-E2E-04 (Ubiquitous)**
The EKS audit log shall show no `create`, `update`, `patch` or `delete` request made by a user whose name starts with `system:serviceaccount:agents:` while handling the incident.

**REQ-E2E-05 (Event-driven)**
When `chaos/oom down` is run, the namespace `chaos-oom` shall be removed.

**REQ-E2E-06 (Event-driven)**
When a change under `agents/` is merged to `main`, then within 10 minutes the workflow shall have pushed images tagged with that commit's SHA and committed the tag to the chart values, and Argo CD shall have rolled the `orchestrator`, `orchestrator-watcher` and `cpu-memory` Deployments to that SHA, with no manual step.

**REQ-E2E-07 (Ubiquitous)**
With the policies applied, all agent pods shall be Ready, and a test pod in the namespace `default` shall fail to connect to the `orchestrator` and `cpu-memory` Services, and the `orchestrator-watcher` pod shall fail to connect to `cpu-memory`.

---

## Out of scope

- Specialists for network, deploy/rollout, storage, and incident memory, and the Knowledge Base ingestion they would use.
- Approving a proposal, opening the `/gitops` pull request, and any cluster write.
- The dashboard, Prometheus and Grafana installation, and dashboards for the metrics.
- Persisting incidents across restarts.
- Authentication between agents. Network isolation between them is in section 9.
- Automatic detection of CPU throttling.

## Assumptions and open questions for review

1. **Delegation over HTTP, not in process.** REQ-ORC-07 makes the orchestrator's tool call the specialist's service. Running the specialist inside the orchestrator would be simpler, but the orchestrator would then need the specialist's Kubernetes read permissions, which breaks the per-agent ServiceAccount separation in `tech.md`.
2. **The watcher is a second Deployment using the orchestrator image.** This avoids a seventh ECR repository and keeps the orchestrator pod free of cluster-wide pod access. The alternative is a thread inside the orchestrator.
3. **No Prometheus exists yet.** The agents expose `/metrics`, but nothing scrapes it, so the cost dashboard needs a separate spec that installs Prometheus and Grafana.
4. **`incident_id` as a label has unbounded cardinality.** `tech.md` requires it. Prometheus will keep a series per incident, which is fine for a demo and a problem at volume.
5. **Metrics-server is added by this spec (REQ-MSV).** The chart has no `kubeVersion` constraint and I have not confirmed it runs on Kubernetes 1.37, so REQ-MSV-03 is the check. The cpu-memory tool still degrades when the metrics API is unavailable (REQ-K8S-06).
6. **The image workflow uses a dedicated ECR push role** (`infra` TASK-16), so it no longer holds Terraform permissions. The repositories are now immutable (TASK-18), so a pushed SHA tag cannot be replaced. The price is REQ-CI-16: the push role needs `ecr:DescribeImages`, which it does not have yet.
7. **CI commits the image tag straight to `main`** (REQ-CI-11). Accepted: the repository has no branch protection and one maintainer. It means anyone who can merge to `main` can deploy to the cluster, with the bot as the committer. If branch protection is added later, the Actions bot must be allowed to bypass it or this job fails.
8. **The network policies limit ports and peers, not destinations.** Bedrock, STS and the EKS API are reached over addresses that a `NetworkPolicy` cannot name, so REQ-NET-06 allows TCP 443 to any address. Restricting it to specific destinations would need VPC interface endpoints, which cost money per hour per availability zone. The Prometheus namespace default (`monitoring`) is a guess, since nothing runs there yet. Internal HTTP still has no authentication.
9. **The proposal is bounded by validation, not by the model.** Logs and events are attacker-controlled text. REQ-CPU-08 limits what a manipulated model can propose, but a human still reads the rationale text.
