# hardening — Requirements

> EARS format, as in `agents-core`. IDs here are new; earlier specs are cited as
> `core:REQ-…` and `spx:REQ-…` (specialists).

## Scope

Live E2E runs of the three chaos scenarios (17 Nov 2026) found two problems.

1. **False alarms.** Readiness `Unhealthy` events while a container starts (for example
   "connection refused" while the server boots) became incidents. The network specialist
   then blamed a NetworkPolicy although its own evidence showed the endpoint `ready=true`.
2. **Duplicate incidents.** One root cause produced two incidents and two LLM
   investigations: PVC `ProvisioningFailed` + pod `FailedScheduling` (pvc); client + server
   `Unhealthy` (netpol); `ErrImagePull` + `Unhealthy` (badimage).

Out of scope: persistence of incidents, multi-replica orchestrator, any Terraform or AWS
change, any write access to the cluster.

## Watcher: startup noise (WSU)

**REQ-WSU-01 (Event-driven)** When the watcher sees an `Unhealthy` event for a pod, it
shall not submit it at once. It shall schedule a re-check no earlier than
`UNHEALTHY_RECHECK_SECONDS` (default 30) after the event and no earlier than
`UNHEALTHY_STARTUP_GRACE_SECONDS` (default 60) after the container started.

**REQ-WSU-02 (Event-driven)** When the re-check runs, the watcher shall read the pod again
and submit the incident only if the pod still exists and is not Ready. A pod that is Ready,
or no longer exists, shall produce no incident.

**REQ-WSU-03 (Event-driven)** When an `Unhealthy` event arrives for a pod that is Ready at
that moment, the watcher shall drop it without scheduling a re-check.

**REQ-WSU-04 (Ubiquitous)** A dropped `Unhealthy` signal shall not be marked as seen, so a
later genuine failure of the same pod is still reported. Repeated events for a pod that
already has a pending re-check shall not add a second re-check.

**REQ-WSU-05 (Ubiquitous)** The container start time is the container's
`state.running.startedAt`; if it is absent, the pod's `status.startTime`; if both are
absent, the event time.

**REQ-WSU-06 (Ubiquitous)** Every incident the watcher submits for a pod shall list in
`depends_on` the PersistentVolumeClaims the pod mounts (`kind: PersistentVolumeClaim`,
claim name). The orchestrator uses this for correlation, so it needs no Kubernetes access
(`core:REQ-CHT-05` stays true).

## Specialists: outcome (OUT)

**REQ-OUT-01 (Ubiquitous)** Every investigation shall carry an `outcome`:
`action_required` (something is broken now), `transient` (it was real but has cleared:
the subject is healthy now), or `no_action` (nothing is wrong).

**REQ-OUT-02 (Ubiquitous)** An investigation with outcome `transient` or `no_action` shall
have no proposal (`proposal: null`). An investigation with outcome `action_required` shall
have a proposal (a change, or `investigate_further`). The model enforces this as an
invariant of `Investigation`.

**REQ-OUT-03 (Event-driven)** When the incident is about a current state (`Unhealthy`,
`ImagePullBackOff`, `ErrImagePull`, `CreateContainerConfigError`, `FailedScheduling`,
`FailedMount`, `FailedAttachVolume`, `ProvisioningFailed`) and code reading the cluster
finds the subject healthy now, the outcome shall be `transient` with no proposal,
whatever the model said. Healthy means: the incident pod is Ready (and the incident
container is ready); for a PVC incident, the PVC is `Bound`; when the pod no longer exists,
the owning Deployment has all replicas updated, ready and available.

**REQ-OUT-04 (Event-driven)** When the model's own evidence shows the incident subject
healthy (an evidence entry naming the incident pod or PVC with ready `true` or phase
`Bound`), proposal validation shall reject the proposal: outcome `transient`, no proposal,
and a validation note quoting the evidence.

**REQ-OUT-05 (Ubiquitous)** REQ-OUT-03 and OUT-04 shall not apply to termination signals
(`OOMKilled`) or `CrashLoopBackOff`: a pod that crashes repeatedly is Ready between
crashes, which does not mean the problem has cleared.

**REQ-OUT-06 (Ubiquitous)** All four specialists (cpu_memory, network, deploy_rollout,
storage) shall use the same outcome logic, and their prompts shall tell the model when to
use each outcome.

## Orchestrator: correlation (COR)

**REQ-COR-01 (Event-driven)** When an incident is submitted, the orchestrator shall look
for a primary incident: same namespace, created within `CORRELATION_WINDOW_SECONDS`
(default 300), still open, and itself not correlated to another one. It matches when
(a) both have the same workload (kind and name), or (b) one incident's workload is in the
other's `depends_on`, or (c) both depend on the same object.

**REQ-COR-02 (Ubiquitous)** An incident is open unless it failed or its investigation
ended `transient` or `no_action`. Several matches pick the oldest.

**REQ-COR-03 (Event-driven)** When a primary is found, the new incident shall be stored
with status `correlated` and `related_to: <primary id>`, shall be appended to the primary's
`related_incidents`, and shall not be classified or investigated (no model call).

**REQ-COR-04 (Ubiquitous)** `GET /incidents/<id>` shall return `related_incidents` (id,
reason, workload, pod, match rule, observed time) for a primary and `related_to` for a
correlated incident.

**REQ-COR-05 (Ubiquitous)** Resubmitting the same incident id stays idempotent
(`core:REQ-ORC-05`): it returns the existing record and correlates nothing.

**REQ-COR-06 (Ubiquitous)** An investigation without a proposal ends with status
`completed`; only a proposal of a change ends `awaiting_approval`.

## Chart (CHH)

**REQ-CHH-01** The chart shall set `UNHEALTHY_STARTUP_GRACE_SECONDS`,
`UNHEALTHY_RECHECK_SECONDS` on the watcher and `CORRELATION_WINDOW_SECONDS` on the
orchestrator from values, with the defaults above.

## Regression (REG)

**REQ-REG-01** Tests shall reproduce each case from the 17 Nov runs through the watcher
and the orchestrator service together, and assert one investigation per scenario:
- netpol: a server `Unhealthy` ("connection refused") during startup that becomes Ready
  produces no incident; the blocked client produces exactly one.
- badimage: `ErrImagePull`, `ImagePullBackOff` and `Unhealthy` on the same Deployment give
  one primary and related incidents, one investigation.
- pvc: PVC `ProvisioningFailed` and pod `FailedScheduling` give one primary and one
  related incident, in either order, one investigation.

**REQ-REG-02** A network specialist test shall reproduce the false diagnosis: a scripted
model proposes a NetworkPolicy change while its evidence shows the incident pod's endpoint
`ready=true`; the result is `transient` with no proposal.
