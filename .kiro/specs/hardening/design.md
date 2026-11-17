# hardening — Design

## 1. Models (`kiroween_shared.models`)

```python
Outcome = Literal["action_required", "transient", "no_action"]
Status += "correlated"

Incident.depends_on: list[Workload] = []          # PVCs the pod mounts (REQ-WSU-06)

SpecialistOutput.outcome: Outcome = "action_required"
SpecialistOutput.proposal: ProposalDraft | None = None

Investigation.outcome: Outcome = "action_required"
Investigation.proposal: Proposal | None = None
# model_validator: proposal is None  <=>  outcome != "action_required"   (REQ-OUT-02)

class RelatedIncident(BaseModel):
    incident_id, reason, workload, pod, match, observed_at
IncidentRecord.related_incidents: list[RelatedIncident] = []
IncidentRecord.related_to: str | None = None
```

Defaults keep every existing producer valid: an `Investigation(proposal=...)` without an
outcome is `action_required`.

## 2. Outcome logic (`kiroween_shared.outcome`, REQ-OUT)

```python
STATE_SIGNALS = {Unhealthy, ImagePullBackOff, ErrImagePull, CreateContainerConfigError,
                 FailedScheduling, FailedMount, FailedAttachVolume, ProvisioningFailed}

def subject_health(incident, kube) -> str | None          # reason it is healthy, or None
def contradicting_evidence(incident, evidence) -> Evidence | None
def decide(incident, outcome, proposal, notes, diagnosis, kube)
    -> (outcome, proposal | None, notes)
```

`decide` runs after the specialist's own validation:

1. Model outcome `transient`/`no_action` → drop the proposal (note if it was a change).
2. Model outcome `action_required` and `incident.reason in STATE_SIGNALS`:
   - own evidence contradicts (REQ-OUT-04) → `transient`, note quotes the entry;
   - else live check healthy (REQ-OUT-03) → `transient`, note gives the reason.
3. Otherwise unchanged.

Evidence match: the entry's field or value names the incident pod (or the PVC for a PVC
incident), and either the field contains the word `ready` with value `true`, or the field
contains `phase` with value `Bound`. Entries about other objects (the server's endpoints in
the real netpol case) never match, so a correct NetworkPolicy fix is not rejected.

Live check reads only what the specialists can already read (`get_pod`, `get_pvc`,
`get_workload`), so no RBAC changes. A read error leaves the outcome unchanged (unknown is
not healthy).

`run_investigation` (network, deploy_rollout, storage) and `cpu_memory.agent` call
`decide`. A missing proposal with outcome `action_required` becomes `investigate_further`
before validation. The common prompt explains the three outcomes.

## 3. Watcher (REQ-WSU)

`Watcher.handle_event` for a Pod `Unhealthy` signal:

```
pod = get_pod(ns, name)
if pod is Ready: drop                                  # WSU-03
due = max(event_time + recheck, container_started + grace)
pending[signal_id] = (signal, due)                     # one per id (WSU-04)
```

`Watcher.tick()` (thread, every 5 s) handles due entries: re-read the pod; gone or Ready →
drop; else `_submit(signal)` (seen-set, startup grace, cooldown as before). Times use the
injectable `now()` so tests drive them. `depends_on` comes from `spec.volumes[*]
.persistentVolumeClaim.claimName` of the pod (status pods for waiting states, the read pod
for events, the termination pod).

Settings: `unhealthy_startup_grace_s` (60), `unhealthy_recheck_s` (30), env
`UNHEALTHY_STARTUP_GRACE_SECONDS`, `UNHEALTHY_RECHECK_SECONDS`.

## 4. Orchestrator correlation (REQ-COR)

`IncidentService.submit` (synchronous, on the event loop, so no lock is needed):

```
existing id → return it (COR-05)
primary = correlate(incident, store.records(), now, window)
if primary:
    store.put(record(status="correlated", related_to=primary.id))
    store.update(primary.id, related_incidents=[..., RelatedIncident(match=rule)])
    return record, True        # 202, not queued
else: queue as before
```

`correlate` scans records newest-first and keeps the oldest open match. Rules in order:
`same_workload`, `depends_on` (either direction), `shared_dependency`. `IncidentStore` gets
`records()`. Status after an investigation: `awaiting_approval` only for a change proposal;
a `None` proposal ends `completed` (COR-06). The orchestrator's tool reply reports
`outcome` and `action` (`none` when there is no proposal).

Window: `CORRELATION_WINDOW_SECONDS`, default 300; `0` disables correlation.

## 5. Chart (REQ-CHH)

`values.yaml`: `watcher.unhealthyStartupGraceSeconds: 60`,
`watcher.unhealthyRecheckSeconds: 30`, `orchestrator.correlationWindowSeconds: 300`, wired
into the two Deployments.

## 6. Regression tests (REQ-REG)

`agents/orchestrator/tests/test_regressions.py` connects a real `Watcher` to a real
`IncidentService` (the watcher client calls `service.submit`), with a fake clock, fake pod
reads, and a counting fake delegate. One test per scenario as listed in REQ-REG-01.
REQ-REG-02 lives in `agents/network/tests`.

## Decisions

- Correlation lives in the orchestrator (one place sees every incident), but the pod →
  PVC link comes from the watcher, which already reads pods. The alternative (orchestrator
  reads pods) would give `orchestrator-sa` Kubernetes permissions it does not have today.
- Startup noise is filtered in the watcher (no model cost); the outcome check in the
  specialists is the second line for anything that still gets through.
- A `transient` result keeps status `completed`, with the outcome visible in the
  investigation. A new status would change the API for no extra information.
