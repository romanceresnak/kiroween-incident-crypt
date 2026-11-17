# specialists — Design

Implements `requirements.md`. `agents-core/design.md` is the base; only the differences
are described here.

## 1. Layout

```
agents/shared/src/kiroween_shared/
├── proposals.py        # set_field rules, path resolution, validate_change()
├── specialist.py       # run_investigation(), create_specialist_app(), specialist_main()
└── k8s/
    ├── client.py       # + services, endpoints, endpointslices, networkpolicies, ingresses,
    │                   #   configmaps, replicasets, controllerrevisions, applications,
    │                   #   pvcs, pvs, storageclasses, volumeattachments, events-for-all
    └── extra_tools.py  # network_tools(), deploy_tools(), storage_tools()
agents/network/          # prompt.py, rules.py, __main__.py, Dockerfile, tests
agents/deploy_rollout/   # same
agents/storage/          # same
chaos/{netpol,badimage,pvc}/  # <name>.sh + manifests
```

`cpu_memory` keeps its own `validate.py` (resource-specific). Moving it onto
`proposals.py` would be a refactor nobody asked for.

## 2. Proposal model (REQ-PRO)

`ProposalDraft` gains the action `set_field` and an optional `target.container`. A
`set_field` draft names `target {kind, namespace, name}`, `field`, `current`, `proposed`.

`field` uses a small path language, resolved by code against the object read from the
cluster: dotted keys, `[N]` list index, and `[name=X]` list match by `name`. Examples:
`spec.selector.app`, `spec.ports[0].targetPort`,
`spec.template.spec.containers[name=web].image`.

```python
@dataclass(frozen=True)
class ChangeRule:
    kinds: frozenset[str]
    field: re.Pattern[str]          # full-match on the path
    check: Callable[[Context, ProposalDraft, object | None], str | None]  # error or None
    target: Callable[[Context, ProposalDraft], str | None] = in_incident_namespace

validate_change(incident, draft, rules, kube) -> (Proposal, notes)
```

Order of checks: action allowed → namespace (REQ-PRO-04) → a rule matches kind and field →
the rule's target check → read the object (missing = rejected) → `current` equals the
observed value (REQ-PRO-03) → the rule's value check. Any failure gives
`investigate_further`, as `cpu_memory` does.

## 3. Specialist runtime (REQ-SPC)

```python
@dataclass(frozen=True)
class SpecialistSpec:
    name: str                      # metrics label and HTTP title, e.g. "network"
    system_prompt: str
    tools: Callable[[KubeReader], list]
    rules: list[ChangeRule]
```

`run_investigation(spec, incident, kube=, model=, settings=, prices=)` does what
`cpu_memory.agent.investigate` does: fresh `Agent` with `CallBudget`, structured output
`SpecialistOutput`, `record_usage`, then `validate_change` instead of the resource
validator. `specialist_main(spec)` reads settings, builds the Bedrock model and
`ReadOnlyKube`, starts metrics on 9090 and serves on 8080. Each package's `__main__.py` is
two lines.

The common prompt tail (the evidence rule, the `<tool_data>` warning and the proposal rules
in prose) lives in `specialist.py`. Each specialist adds its own diagnosis section.

## 4. Tools

| Tool | Agents | Reads |
|---|---|---|
| shared `get_pod`, `list_pods`, `get_pod_logs`, `list_events`, `get_owner_workload`, `get_node` | all | as today |
| `get_service`, `list_services` | network | Service spec |
| `get_endpoints` | network | EndpointSlices for a Service (ready/not-ready counts and addresses) |
| `list_network_policies` | network | policies in a namespace with pod/namespace selectors and ports |
| `list_ingresses` | network | rules, backends, class, load balancer status |
| `get_coredns_status` | network | CoreDNS pods, recent log lines, Corefile |
| `get_rollout_history` | deploy-rollout | ReplicaSets of a workload with revision, image, ready replicas |
| `get_workload_status` | deploy-rollout | conditions, observed generation, updated/available replicas |
| `list_configmap_keys` | deploy-rollout | ConfigMap key names only |
| `get_argocd_application` | deploy-rollout | sync and health status, conditions, operation message |
| `get_pvc`, `list_pvcs` | storage | spec, phase, conditions |
| `get_pv` | storage | spec, phase, claim |
| `list_storage_classes` | storage | name, provisioner, expansion, default flag |
| `list_volume_attachments` | storage | attacher, node, attached, errors |
| `get_ebs_csi_status` | storage | `ebs-csi-controller` pods and recent log lines |

ConfigMaps other than the Corefile return key names only (REQ-TLS-02). The read-only
AST guard covers the new modules because it scans the whole `k8s` package.

## 5. Change rules (REQ-PRO-05)

**network**
- Service `spec.selector.<key>`: label value regex.
- Service `spec.ports[<i>].targetPort`: integer 1–65535 or IANA-style name.
- NetworkPolicy `spec.ingress[<i>].from[<j>].podSelector.matchLabels.<key>`: label value,
  and some pod in the namespace must carry `<key>=<value>`. This keeps a fix tied to a real
  workload instead of opening the policy.

**deploy-rollout** (target must be the incident's workload)
- `spec.template.spec.containers[name=<c>].image`: the proposed image must equal the image
  of an earlier ReplicaSet revision of this workload. A specialist cannot invent a tag; for
  a first deployment with no good revision the answer is `investigate_further`.

**storage** (target must be a PVC the incident is about: the incident's PVC, or one the
incident pod mounts)
- `spec.storageClassName`: an existing StorageClass, and the PVC is `Pending`. The field is
  immutable, so the risk text must say the PVC is recreated (no data, since it never bound).
- `spec.resources.requests.storage`: larger, at most 4×, and the StorageClass has
  `allowVolumeExpansion: true`.

## 6. Orchestrator (REQ-ORX)

- `Classification` gains `confidence`. `classify()` then applies two guards: low confidence
  becomes `unknown`, and for a known watcher signal a category outside the allowed set
  becomes `unknown` with the reason in the rationale.
- `SPECIALISTS` becomes a registry `{category: SpecialistClient}` built from
  `CPU_MEMORY_URL`, `NETWORK_URL`, `DEPLOY_ROLLOUT_URL`, `STORAGE_URL`.
- `make_specialist_tool(category, …)` uses `@tool(name=f"investigate_{category}")`; the
  delegation agent gets only that tool. Usage is recorded under the specialist's name.

## 7. Watcher (REQ-WTX)

- `detect.waiting_states(pod)` raises incidents for the waiting reasons. The id hashes
  namespace, workload, container, reason and the pod uid, so one stuck pod is one incident.
- `detect.event_incident(event, pod)` maps a Pod or PVC event to an incident. For Pod
  events the watcher reads the pod (it already has `get pods`) to resolve the workload.
  The id hashes namespace, involved kind and name, reason and the involved object uid.
- `run()` runs twice in two threads: one stream for pods, one for events. Both feed the
  same `Watcher`, so cooldown and seen-set are shared.
- Events older than the startup grace are ignored using `lastTimestamp`/`eventTime`.

## 8. Chart

`values.specialists` lists `network`, `deployRollout`, `storage` with service name,
ServiceAccount, image, model and resources. The specialist Deployments, Services and
network policies are rendered by one `range` over that list, so the three are identical
except for RBAC, which is per agent in `rbac.yaml`. The existing per-agent policies become
`allow-orchestrator-to-<agent>` and `allow-orchestrator-egress`, which lists every
specialist.

## 9. CI

The push matrix gains `network`, `deploy_rollout`, `storage` →
`kiroween/network`, `kiroween/deploy-rollout`, `kiroween/storage`. `update-chart` passes
all five components to `set_image_tag.py`.

## 10. Expected chaos outcomes

| Scenario | Watcher signal | Category | Expected proposal |
|---|---|---|---|
| netpol | `Unhealthy` on `netpol-client` | network | `set_field` NetworkPolicy `allow-trusted` `spec.ingress[0].from[0].podSelector.matchLabels.app`: `netpol-trusted` → `netpol-client` |
| badimage | `ImagePullBackOff`/`ErrImagePull` on `badimage-demo` | deploy_rollout | `set_field` Deployment `badimage-demo` `spec.template.spec.containers[name=web].image`: bad tag → revision 1 image |
| pvc | `ProvisioningFailed` on PVC `data`, `FailedScheduling` on the pod | storage | `set_field` PVC `data` `spec.storageClassName`: `does-not-exist` → `gp2` |

The model decides the proposal; validation only guarantees that whatever it proposes is
within these bounds. A model that answers `investigate_further` passes validation too, so
the acceptance run checks the actual output.
