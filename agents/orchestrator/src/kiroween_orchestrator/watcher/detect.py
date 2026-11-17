"""Turn pod status into incidents (REQ-WAT-02, WAT-09).

Works on plain dicts (the serialized pod), so it is tested without a cluster.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from kiroween_shared.models import Incident


@dataclass(frozen=True)
class Termination:
    namespace: str
    workload_kind: str
    workload_name: str
    pod: str
    container: str
    reason: str
    exit_code: int | None
    finished_at: datetime


def parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def resolve_workload(pod: dict[str, Any]) -> tuple[str, str]:
    """The workload that owns a pod, using only the pod (the watcher can only read pods).

    A ReplicaSet created by a Deployment is named <deployment>-<pod-template-hash>, so the
    Deployment name is the ReplicaSet name without that suffix.
    """
    meta = pod.get("metadata") or {}
    owners = meta.get("ownerReferences") or []
    if not owners:
        return "Pod", meta.get("name", "")
    owner = next((o for o in owners if o.get("controller")), owners[0])
    kind, name = owner.get("kind", ""), owner.get("name", "")
    if kind == "ReplicaSet":
        template_hash = (meta.get("labels") or {}).get("pod-template-hash")
        if template_hash and name.endswith(f"-{template_hash}"):
            return "Deployment", name[: -len(template_hash) - 1]
    return kind, name


def terminations(pod: dict[str, Any], reasons: set[str]) -> list[Termination]:
    meta = pod.get("metadata") or {}
    kind, name = resolve_workload(pod)
    found: dict[tuple[str, datetime], Termination] = {}
    for status in (pod.get("status") or {}).get("containerStatuses") or []:
        for key in ("state", "lastState"):
            terminated = (status.get(key) or {}).get("terminated")
            if not terminated or terminated.get("reason") not in reasons:
                continue
            finished = parse_time(terminated.get("finishedAt"))
            if finished is None:
                continue  # cannot deduplicate a termination without a time
            container = status.get("name", "")
            found[(container, finished)] = Termination(
                namespace=meta.get("namespace", ""),
                workload_kind=kind,
                workload_name=name,
                pod=meta.get("name", ""),
                container=container,
                reason=terminated["reason"],
                exit_code=terminated.get("exitCode"),
                finished_at=finished,
            )
    return list(found.values())


def incident_id(t: Termination) -> str:
    """Same termination, same id: replays are idempotent at the orchestrator."""
    key = "|".join([t.namespace, t.workload_kind, t.workload_name, t.container, t.reason,
                    t.finished_at.isoformat()])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def to_incident(t: Termination, observed_at: datetime) -> Incident:
    return Incident(
        incident_id=incident_id(t),
        source="watcher",
        reason=t.reason,
        namespace=t.namespace,
        workload={"kind": t.workload_kind, "name": t.workload_name},
        pod=t.pod,
        container=t.container,
        message=f"Container {t.container} terminated: {t.reason} (exit code {t.exit_code})",
        observed_at=observed_at,
    )
