"""Decide whether an investigation needs action (hardening REQ-OUT).

A specialist's proposal is only useful if the subject of the incident is still broken.
``decide`` runs after a specialist's own proposal validation and turns the result into
``transient`` (no proposal) when the model said so, when the model's own evidence shows the
subject healthy, or when code reading the cluster finds it healthy now.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .models import Diagnosis, Evidence, Incident, Outcome, Proposal

logger = logging.getLogger(__name__)

# Signals that describe a current state. A healthy subject means the state has cleared.
# Terminations (OOMKilled) and CrashLoopBackOff are excluded: a pod that keeps crashing is
# Ready between crashes (REQ-OUT-05).
STATE_SIGNALS = frozenset({
    "Unhealthy", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError",
    "FailedScheduling", "FailedMount", "FailedAttachVolume", "ProvisioningFailed",
})
CHANGE_ACTIONS = frozenset({"adjust_resources", "set_field"})

_READY = re.compile(r"\bready\b", re.I)
_PHASE = re.compile(r"\bphase\b", re.I)


def _condition(obj: dict[str, Any], kind: str) -> str | None:
    for cond in (obj.get("status") or {}).get("conditions") or []:
        if cond.get("type") == kind:
            return str(cond.get("status"))
    return None


def pod_is_ready(pod: dict[str, Any], container: str | None = None) -> bool:
    if _condition(pod, "Ready") != "True":
        return False
    if container is None:
        return True
    for status in (pod.get("status") or {}).get("containerStatuses") or []:
        if status.get("name") == container:
            return bool(status.get("ready"))
    return False


def deployment_is_healthy(deployment: dict[str, Any]) -> bool:
    spec, status = deployment.get("spec") or {}, deployment.get("status") or {}
    want = spec.get("replicas", 1)
    meta = deployment.get("metadata") or {}
    return (
        status.get("observedGeneration", 0) >= meta.get("generation", 0)
        and status.get("updatedReplicas", 0) == want
        and status.get("readyReplicas", 0) == want
        and status.get("availableReplicas", 0) == want
        and not status.get("unavailableReplicas")
    )


def subject_health(incident: Incident, kube: Any) -> str | None:
    """Why the incident's subject is healthy now, or None (not healthy, or unknown)."""
    from .k8s.client import KubeError

    ns, workload = incident.namespace, incident.workload
    try:
        if workload.kind == "PersistentVolumeClaim":
            pvc = kube.get_pvc(ns, workload.name)
            phase = (pvc.get("status") or {}).get("phase")
            return f"PVC {workload.name} is Bound" if phase == "Bound" else None
        if incident.pod:
            try:
                pod = kube.get_pod(ns, incident.pod)
            except KubeError as exc:
                if exc.status != 404:
                    raise
            else:
                if pod_is_ready(pod, incident.container):
                    return f"pod {incident.pod} is Ready"
                return None
        # The pod is gone (replaced) or the incident names no pod: look at the workload.
        if workload.kind == "Deployment":
            deployment = kube.get_workload("Deployment", ns, workload.name)
            if deployment_is_healthy(deployment):
                return f"Deployment {workload.name} has all replicas updated and available"
    except KubeError as exc:
        logger.info("health re-check failed (%s); keeping the outcome", exc.reason)
    except Exception:  # unknown is not healthy
        logger.exception("health re-check failed; keeping the outcome")
    return None


def contradicting_evidence(incident: Incident, evidence: list[Evidence]) -> Evidence | None:
    """An evidence entry that shows the incident's own subject healthy (REQ-OUT-04)."""
    if incident.workload.kind == "PersistentVolumeClaim":
        subject = incident.workload.name
    elif incident.pod:
        subject = incident.pod
    else:
        return None
    pattern = re.compile(rf"(?<![a-z0-9-]){re.escape(subject)}(?![a-z0-9-])", re.I)
    for entry in evidence:
        if not pattern.search(f"{entry.field} {entry.value}"):
            continue
        value = entry.value.strip().strip("\"'").lower()
        if _READY.search(entry.field) and value == "true":
            return entry
        if _PHASE.search(entry.field) and value == "bound":
            return entry
    return None


def decide(
    incident: Incident,
    outcome: Outcome,
    proposal: Proposal,
    notes: list[str],
    diagnosis: Diagnosis,
    kube: Any,
) -> tuple[Outcome, Proposal | None, list[str]]:
    notes = list(notes)
    if outcome != "action_required":
        if proposal.action in CHANGE_ACTIONS:
            notes.append(f"proposal dropped: the specialist reported outcome {outcome}")
        return outcome, None, notes
    if incident.reason not in STATE_SIGNALS:
        return outcome, proposal, notes
    entry = contradicting_evidence(incident, diagnosis.evidence)
    if entry is not None:
        notes.append(
            "proposal rejected: its own evidence shows the subject healthy "
            f"({entry.tool} {entry.field} = {entry.value})"
        )
        return "transient", None, notes
    healthy = subject_health(incident, kube)
    if healthy is not None:
        notes.append(f"no action: {healthy} now")
        return "transient", None, notes
    return outcome, proposal, notes
