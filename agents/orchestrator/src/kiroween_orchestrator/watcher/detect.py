"""Turn pod status into incidents (REQ-WAT-02, WAT-09).

Works on plain dicts (the serialized pod), so it is tested without a cluster.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from kiroween_shared.models import ContainerResources, Incident


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


# --- generic signals: terminations, waiting states and events (specialists REQ-WTX) ----

WAITING_REASONS = frozenset({"ImagePullBackOff", "ErrImagePull", "CrashLoopBackOff",
                             "CreateContainerConfigError"})
EVENT_REASONS: dict[str, frozenset[str]] = {
    "Pod": frozenset({"FailedScheduling", "FailedMount", "FailedAttachVolume", "Unhealthy"}),
    "PersistentVolumeClaim": frozenset({"ProvisioningFailed"}),
}
_FIELD_PATH_CONTAINER = re.compile(r"spec\.(?:initContainers|containers)\{([^}]+)\}")


@dataclass(frozen=True)
class Signal:
    namespace: str
    workload_kind: str
    workload_name: str
    pod: str | None
    container: str | None
    reason: str
    # What makes this occurrence unique: the termination time, the pod, or the object.
    identity: str
    # When it happened; None for an ongoing state (always current, so never "too old").
    at: datetime | None
    message: str
    # (kind, name) of objects the pod needs: the PVCs it mounts (hardening REQ-WSU-06).
    depends_on: tuple[tuple[str, str], ...] = ()
    # What the failing container may use and is using; read for an Unhealthy probe only.
    resources: ContainerResources | None = None


_CPU_QUANTITY = re.compile(r"(\d+(?:\.\d+)?)([num]?)")


def parse_cpu_millicores(value: Any) -> int | None:
    """A Kubernetes CPU quantity as whole millicores: "20m", "0.5", "2", "250u", "19739000n"."""
    if isinstance(value, bool) or value is None:
        return None
    match = _CPU_QUANTITY.fullmatch(str(value).strip())
    if match is None:
        return None
    scale = {"n": 1e-6, "u": 1e-3, "m": 1.0, "": 1000.0}[match.group(2)]
    amount = float(match.group(1)) * scale
    return round(amount) if amount <= 10_000_000 else None


def container_resources(
    pod: dict[str, Any] | None, container: str | None, usage_millicores: int | None = None
) -> ContainerResources | None:
    """The named container's CPU limit and request from the pod spec, plus its measured usage.

    None when there is nothing to report, so an incident without a hint is unchanged."""
    if not pod:
        return None
    spec = pod.get("spec") or {}
    containers = spec.get("containers") or []
    if container is None and len(containers) == 1:  # an event without a field path
        container = containers[0].get("name")
    if not container:
        return None
    for c in [*containers, *(spec.get("initContainers") or [])]:
        if c.get("name") == container:
            res = c.get("resources") or {}
            limit = parse_cpu_millicores((res.get("limits") or {}).get("cpu"))
            request = parse_cpu_millicores((res.get("requests") or {}).get("cpu"))
            if limit is None and request is None and usage_millicores is None:
                return None
            return ContainerResources(cpu_limit_millicores=limit, cpu_request_millicores=request,
                                      cpu_usage_millicores=usage_millicores)
    return None


def usage_millicores_of(metrics: dict[str, Any] | None, container: str | None) -> int | None:
    """The named container's CPU in a metrics.k8s.io PodMetrics object, if it has one."""
    for c in (metrics or {}).get("containers") or []:
        if c.get("name") == container:
            return parse_cpu_millicores((c.get("usage") or {}).get("cpu"))
    return None


def pod_claims(pod: dict[str, Any] | None) -> tuple[tuple[str, str], ...]:
    claims = []
    for volume in ((pod or {}).get("spec") or {}).get("volumes") or []:
        name = (volume.get("persistentVolumeClaim") or {}).get("claimName")
        if name and ("PersistentVolumeClaim", name) not in claims:
            claims.append(("PersistentVolumeClaim", name))
    return tuple(claims)


def pod_ready(pod: dict[str, Any]) -> bool:
    for cond in (pod.get("status") or {}).get("conditions") or []:
        if cond.get("type") == "Ready":
            return cond.get("status") == "True"
    return False


def container_started(pod: dict[str, Any], container: str | None) -> datetime | None:
    """The container's running.startedAt, else the pod's startTime (REQ-WSU-05)."""
    status = pod.get("status") or {}
    for c in status.get("containerStatuses") or []:
        if c.get("name") == container:
            started = ((c.get("state") or {}).get("running") or {}).get("startedAt")
            if started:
                return parse_time(started)
    start = status.get("startTime")
    return parse_time(start) if start else None


def signal_id(s: Signal) -> str:
    key = "|".join([s.namespace, s.workload_kind, s.workload_name, s.container or "",
                    s.reason, s.identity])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def termination_signal(t: Termination) -> Signal:
    # Same fields as incident_id(t), so OOMKilled ids are unchanged.
    return Signal(t.namespace, t.workload_kind, t.workload_name, t.pod, t.container, t.reason,
                  t.finished_at.isoformat(), t.finished_at,
                  f"Container {t.container} terminated: {t.reason} (exit code {t.exit_code})")


def waiting_signals(
    pod: dict[str, Any], reasons: frozenset[str], reported: frozenset[str] = frozenset()
) -> list[Signal]:
    """Containers waiting for one of ``reasons``. A crash loop whose last termination is
    already reported on its own (``reported``, e.g. OOMKilled) is skipped, so one OOM does
    not become two incidents."""
    meta = pod.get("metadata") or {}
    kind, name = resolve_workload(pod)
    signals = []
    for status in (pod.get("status") or {}).get("containerStatuses") or []:
        waiting = (status.get("state") or {}).get("waiting") or {}
        last = ((status.get("lastState") or {}).get("terminated") or {}).get("reason")
        if waiting.get("reason") == "CrashLoopBackOff" and last in reported:
            continue
        if waiting.get("reason") in reasons:
            signals.append(Signal(
                meta.get("namespace", ""), kind, name, meta.get("name"), status.get("name"),
                waiting["reason"], f"pod:{meta.get('uid') or meta.get('name')}", None,
                f"{waiting['reason']}: {str(waiting.get('message') or '')[:500]}",
                pod_claims(pod)))
    return signals


def event_signal(
    event: dict[str, Any],
    reasons: Mapping[str, frozenset[str]],
    get_pod: Callable[[str, str], dict[str, Any] | None] | None = None,
) -> Signal | None:
    involved = event.get("involvedObject") or {}
    kind, name, reason = involved.get("kind"), involved.get("name"), event.get("reason")
    if not kind or not name or reason not in reasons.get(kind, frozenset()):
        return None
    namespace = involved.get("namespace") or (event.get("metadata") or {}).get("namespace", "")
    at = parse_time(event.get("lastTimestamp") or event.get("eventTime")
                    or event.get("firstTimestamp")
                    or (event.get("metadata") or {}).get("creationTimestamp"))
    identity = f"{kind}:{involved.get('uid') or name}"
    message = f"{reason}: {str(event.get('message') or '')[:500]}"
    if kind == "PersistentVolumeClaim":
        return Signal(namespace, kind, name, None, None, reason, identity, at, message)
    pod = get_pod(namespace, name) if get_pod else None
    workload_kind, workload_name = resolve_workload(pod) if pod else ("Pod", name)
    match = _FIELD_PATH_CONTAINER.search(involved.get("fieldPath") or "")
    return Signal(namespace, workload_kind, workload_name, name,
                  match.group(1) if match else None, reason, identity, at, message,
                  pod_claims(pod))


def signal_to_incident(s: Signal, observed_at: datetime) -> Incident:
    return Incident(
        incident_id=signal_id(s),
        source="watcher",
        reason=s.reason,
        namespace=s.namespace,
        workload={"kind": s.workload_kind, "name": s.workload_name},
        pod=s.pod,
        container=s.container,
        message=s.message[:2000],
        observed_at=observed_at,
        depends_on=[{"kind": kind, "name": name} for kind, name in s.depends_on],
        resources=s.resources,
    )
