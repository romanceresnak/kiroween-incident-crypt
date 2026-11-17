"""Attach an incident to an open incident with the same root cause (hardening REQ-COR).

One fault often produces several signals: a PVC that never binds (ProvisioningFailed) and
the pod that mounts it (FailedScheduling), or a pod that cannot pull its image
(ErrImagePull, then ImagePullBackOff). Each would otherwise cost its own investigation.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Literal

from kiroween_shared.models import Incident, IncidentRecord

Match = Literal["same_workload", "depends_on", "shared_dependency"]


def _key(kind: str, name: str) -> tuple[str, str]:
    return kind, name


def is_open(record: IncidentRecord) -> bool:
    """Open unless it failed or its investigation found nothing to do (REQ-COR-02)."""
    if record.status in ("failed", "correlated") or record.related_to is not None:
        return False
    inv = record.investigation
    return inv is None or inv.outcome == "action_required"


def match(new: Incident, old: Incident) -> Match | None:
    new_workload = _key(new.workload.kind, new.workload.name)
    old_workload = _key(old.workload.kind, old.workload.name)
    if new_workload == old_workload:
        return "same_workload"
    new_deps = {_key(d.kind, d.name) for d in new.depends_on}
    old_deps = {_key(d.kind, d.name) for d in old.depends_on}
    if old_workload in new_deps or new_workload in old_deps:
        return "depends_on"
    if new_deps & old_deps:
        return "shared_dependency"
    return None


def correlate(
    incident: Incident,
    records: Iterable[IncidentRecord],
    now: datetime,
    window_s: float,
) -> tuple[IncidentRecord, Match] | None:
    """The oldest open incident in the window that matches, and how (REQ-COR-01)."""
    if window_s <= 0:
        return None
    oldest = now - timedelta(seconds=window_s)
    best: tuple[IncidentRecord, Match] | None = None
    for record in records:
        old = record.incident
        if (old.incident_id == incident.incident_id or old.namespace != incident.namespace
                or record.created_at < oldest or not is_open(record)):
            continue
        rule = match(incident, old)
        if rule and (best is None or record.created_at < best[0].created_at):
            best = (record, rule)
    return best
