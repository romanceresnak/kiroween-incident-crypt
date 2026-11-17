# specialists — Requirements

> EARS format, as in `agents-core`. IDs here are new; `agents-core` IDs are cited as
> `core:REQ-…`.

## Scope

Three specialist agents built like `agents/cpu_memory`, wired into the existing
orchestrator, watcher, chart and CI, plus three chaos scenarios.

| Agent | Package | Diagnoses |
|---|---|---|
| network | `agents/network` | DNS (CoreDNS), NetworkPolicy blocking traffic, Service selector or port mismatch, Endpoints/EndpointSlices with no ready address, Ingress errors |
| deploy-rollout | `agents/deploy_rollout` | ImagePullBackOff/ErrImagePull, CrashLoopBackOff or CreateContainerConfigError from bad config, stuck or failed rollouts, Argo CD Application sync/health failures |
| storage | `agents/storage` | PVC Pending (missing or invalid StorageClass, provisioning errors), FailedMount/FailedAttachVolume, EBS CSI errors, node disk pressure |

**Reused, not changed:** the Pod Identity roles, associations (`network-sa`,
`deploy-rollout-sa`, `storage-sa` in `agents`) and ECR repositories from `infra`
(checked live on 17 Nov 2026). No Terraform change is in scope.

**Model:** `eu.anthropic.claude-sonnet-4-6` for all three, as a chart value.

## 1. Shared specialist runtime

**REQ-SPC-01 (Ubiquitous)** `agents/shared` shall provide a specialist runtime that runs one Strands agent per investigation with the specialist's prompt, tools and structured output, records usage (`core:REQ-MET-01`), validates the proposal in code, and serves `POST /investigate`, `/healthz`, `/readyz` on the API port and `/metrics` on the metrics port, as `cpu_memory` does (`core:REQ-CPU-01`).

**REQ-SPC-02 (Ubiquitous)** Each new specialist package shall contain only its prompt, its tool selection, its proposal rules and its entrypoint, and shall use the shared runtime for everything else.

**REQ-SPC-03 (Ubiquitous)** `agents/cpu_memory` shall keep working unchanged; its tests shall still pass.

## 2. Proposals

**REQ-PRO-01 (Ubiquitous)** A new specialist proposal shall be either `investigate_further`, `none`, or `set_field`: one change to one field of one named object (`kind`, `namespace`, `name`, `field` path, `current`, `proposed`), with `requires_approval: true` and the fixed delivery text (pull request against `/gitops` after approval).

**REQ-PRO-02 (Ubiquitous)** Code shall validate every `set_field` proposal against the specialist's rules before it is returned. A proposal that fails any check shall be replaced with `investigate_further`, the diagnosis confidence set to `low`, and the reasons listed (as `core:REQ-CPU-08`).

**REQ-PRO-03 (Ubiquitous)** For every `set_field` proposal, code shall read the target object from the cluster and require that `current` equals the observed value at `field`; `current` may be null only when the field is absent.

**REQ-PRO-04 (Ubiquitous)** The target namespace shall equal the incident's namespace, and shall never be `kube-system`, `kube-public`, `kube-node-lease`, `argocd` or `agents`.

**REQ-PRO-05 (Ubiquitous)** Each specialist shall allow only these changes:

| Specialist | Kind | Field | Proposed value must |
|---|---|---|---|
| network | Service | `spec.selector.<key>` | be a valid label value |
| network | Service | `spec.ports[<i>].targetPort` | be a port 1–65535 or a valid port name |
| network | NetworkPolicy | `spec.ingress[<i>].from[<j>].podSelector.matchLabels.<key>` | be a valid label value matching a label on a pod in the namespace |
| deploy-rollout | the incident's workload | `spec.template.spec.containers[name=<c>].image` | be an image this workload ran in an earlier ReplicaSet revision |
| storage | PersistentVolumeClaim used by the incident | `spec.storageClassName` | name an existing StorageClass, and the PVC must be `Pending` |
| storage | PersistentVolumeClaim used by the incident | `spec.resources.requests.storage` | be an increase of at most 4×, and the StorageClass must allow expansion |

**REQ-PRO-06 (Ubiquitous)** No specialist shall propose a change to a Secret, to environment variable values, or to an Argo CD Application. Those findings shall be `investigate_further` with the diagnosis naming what to change.

## 3. Read-only tools and RBAC

**REQ-TLS-01 (Ubiquitous)** All new tools shall live in `agents/shared` under the read-only guard (`core:REQ-K8S-02`), return bounded `<tool_data>` results, and turn API errors into structured results.

**REQ-TLS-02 (Ubiquitous)** No tool shall read a Secret. Tools that read a ConfigMap shall return its key names only, except the CoreDNS Corefile.

**REQ-TLS-03 (Ubiquitous)** Each ServiceAccount shall get only `get`, `list`, `watch` on the resources its tools read:

| ServiceAccount | Resources |
|---|---|
| `network-sa` | core `pods`, `pods/log`, `services`, `endpoints`, `events`; `discovery.k8s.io` `endpointslices`; `networking.k8s.io` `networkpolicies`, `ingresses`, `ingressclasses`; a Role in `kube-system` for `configmaps` named `coredns` |
| `deploy-rollout-sa` | core `pods`, `pods/log`, `events`, `configmaps`; `apps` `deployments`, `replicasets`, `statefulsets`, `daemonsets`, `controllerrevisions`; a Role in `argocd` for `argoproj.io` `applications` |
| `storage-sa` | core `pods`, `pods/log`, `events`, `persistentvolumeclaims`, `persistentvolumes`, `nodes`; `storage.k8s.io` `storageclasses`, `volumeattachments`, `csidrivers`, `csinodes` |

**REQ-TLS-04 (Ubiquitous)** The watcher's ServiceAccount shall additionally get `get`, `list`, `watch` on core `events`.

## 4. Orchestrator

**REQ-ORX-01 (Ubiquitous)** The orchestrator shall delegate `network`, `deploy_rollout` and `storage` to the matching specialist over HTTP, one Strands tool per specialist (`core:REQ-ORC-07`). A category whose URL is not configured shall end as `no_specialist`.

**REQ-ORX-02 (Ubiquitous)** The classifier shall return a confidence (`low`, `medium`, `high`). A `low` confidence shall be treated as `unknown`.

**REQ-ORX-03 (Ubiquitous)** For incidents from the watcher, the category shall be one of those allowed for the signal; otherwise it shall become `unknown` with the reason recorded:

| Signal | Allowed categories |
|---|---|
| `OOMKilled` | cpu_memory |
| `ImagePullBackOff`, `ErrImagePull`, `CreateContainerConfigError` | deploy_rollout |
| `CrashLoopBackOff` | deploy_rollout, cpu_memory |
| `FailedScheduling` | storage, cpu_memory |
| `ProvisioningFailed`, `FailedMount`, `FailedAttachVolume` | storage |
| `Unhealthy` | network, deploy_rollout |

**REQ-ORX-04 (Ubiquitous)** The delegation agent shall only see the tool for the incident's category, so it cannot route to another specialist.

## 5. Watcher

**REQ-WTX-01 (Event-driven)** When a container is waiting with reason `ImagePullBackOff`, `ErrImagePull`, `CrashLoopBackOff` or `CreateContainerConfigError`, the watcher shall raise an incident.

**REQ-WTX-02 (Event-driven)** When an Event with reason `FailedScheduling`, `FailedMount`, `FailedAttachVolume` or `Unhealthy` is recorded for a Pod, or `ProvisioningFailed` for a PersistentVolumeClaim, the watcher shall raise an incident.

**REQ-WTX-03 (Ubiquitous)** New incidents shall use the same cooldown, seen-set, startup grace, delivery retry and namespace exclusions as `OOMKilled` (`core:REQ-WAT-03` to `WAT-06`). Their ids shall be stable for the same object and reason, so a repeat is idempotent at the orchestrator.

**REQ-WTX-04 (Ubiquitous)** An incident about a PersistentVolumeClaim shall have workload `{kind: PersistentVolumeClaim, name}` and no pod or container. `pod` and `container` become optional in the incident model.

## 6. Chart

**REQ-CHX-01 (Ubiquitous)** The chart shall deploy each specialist with ServiceAccount `<agent>-sa`, the RBAC in REQ-TLS-03, a Deployment and Service like `cpu-memory` (`core:REQ-CHT-06`), and only once its image tag is set.

**REQ-CHX-02 (Ubiquitous)** Each specialist shall accept ingress on the API port only from the orchestrator; the orchestrator shall have egress to each specialist; each specialist shall have DNS, TCP 443 and Pod Identity Agent egress.

**REQ-CHX-03 (Ubiquitous)** Each specialist's image tag line shall be CI-managed (`# ci-managed:<agent>`).

## 7. CI

**REQ-CIX-01 (Ubiquitous)** The image workflow shall test, build and push all five images (`orchestrator`, `cpu_memory`, `network`, `deploy_rollout`, `storage`) and the tag job shall update all five tags in one commit.

## 8. Chaos scenarios

**REQ-CHS-01 (Ubiquitous)** `chaos/netpol`, `chaos/badimage` and `chaos/pvc` shall each have `up` and `down`, refuse a wrong kubectl context, act only on their own namespace, be idempotent, be shellcheck-clean, and have tests with a fake `kubectl` (as `core:REQ-CHO`).

**REQ-CHS-02** `chaos/netpol`: a server and a client in `chaos-netpol`; a NetworkPolicy lets only pods labelled `app: netpol-trusted` reach the server; the client (`app: netpol-client`) fails its readiness probe, which the watcher reports as `Unhealthy`.

**REQ-CHS-03** `chaos/badimage`: in `chaos-badimage`, a Deployment first rolls out a working image, then is updated to a tag that does not exist, so revision 2 is in `ImagePullBackOff` and revision 1 is the known-good image.

**REQ-CHS-04** `chaos/pvc`: in `chaos-pvc`, a PVC with a StorageClass that does not exist and a Deployment that mounts it, so the PVC is `Pending` with `ProvisioningFailed` and the pod is `Pending` with `FailedScheduling`.

## 9. Acceptance

**REQ-ACC-01 (Ubiquitous)** Unit tests shall cover, for each specialist: every allowed change, every rejection rule, a prompt-injection case in cluster text, and missing evidence.

**REQ-ACC-02 (Event-driven)** When each chaos scenario runs on the cluster, an incident shall reach the matching specialist and end `awaiting_approval` with the expected proposal (tasks.md lists them), and no write request from `system:serviceaccount:agents:*` shall appear in the audit log.

## Out of scope

Applying proposals, an incident-memory agent, CPU throttling detection, NetworkPolicy
egress diagnosis beyond what the tools read, and changes to Terraform.
