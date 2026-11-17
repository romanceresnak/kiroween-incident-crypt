"""Code-side checks on the model's proposal (REQ-CPU-06 to CPU-09).

The model reads attacker-controllable text (logs, event messages). These checks bound
what any proposal can say, whatever the model was told (REQ-CPU-10).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from kiroween_shared.models import Incident, Proposal, ProposalDraft

FIELDS: dict[str, tuple[str, str]] = {
    "resources.limits.memory": ("limits", "memory"),
    "resources.requests.memory": ("requests", "memory"),
    "resources.limits.cpu": ("limits", "cpu"),
    "resources.requests.cpu": ("requests", "cpu"),
}

_MULTIPLIERS: dict[str, Decimal] = {
    "": Decimal(1),
    "m": Decimal("0.001"),
    "k": Decimal(10**3), "K": Decimal(10**3), "M": Decimal(10**6), "G": Decimal(10**9),
    "T": Decimal(10**12),
    "Ki": Decimal(2**10), "Mi": Decimal(2**20), "Gi": Decimal(2**30), "Ti": Decimal(2**40),
}
_QUANTITY = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(Ki|Mi|Gi|Ti|m|k|K|M|G|T)?$")


def parse_quantity(value: str | None) -> Decimal | None:
    """Parse a Kubernetes quantity such as 64Mi, 0.5Gi, 500m or 2. None if invalid."""
    if value is None:
        return None
    match = _QUANTITY.match(value.strip())
    if not match:
        return None
    try:
        return Decimal(match.group(1)) * _MULTIPLIERS[match.group(2) or ""]
    except InvalidOperation:
        return None


def observed_values(containers: list[dict[str, Any]], container: str) -> dict[str, str]:
    """Resource values of one container, keyed by proposal field name."""
    for item in containers:
        if item.get("name") == container:
            resources = item.get("resources") or {}
            values = {}
            for field, (section, resource) in FIELDS.items():
                value = (resources.get(section) or {}).get(resource)
                if value is not None:
                    values[field] = str(value)
            return values
    return {}


def validate(
    incident: Incident,
    draft: ProposalDraft,
    observed: dict[str, str],
    *,
    max_factor: Decimal = Decimal(4),
) -> tuple[Proposal, list[str]]:
    """Return the proposal to send and the reasons it was changed, if any."""
    if draft.action != "adjust_resources":
        return Proposal(action=draft.action, rationale=draft.rationale, risk=draft.risk), []

    notes: list[str] = []
    target = draft.target
    expected = (incident.namespace, incident.workload.kind, incident.workload.name,
                incident.container)
    if target is None or (target.namespace, target.kind, target.name, target.container) != expected:
        notes.append("target does not match the incident's workload and container")

    if draft.field not in FIELDS:
        notes.append(f"field {draft.field!r} is not a resource request or limit")
    else:
        seen = observed.get(draft.field)
        current = parse_quantity(draft.current)
        proposed = parse_quantity(draft.proposed)
        seen_value = parse_quantity(seen)
        if seen_value is None:
            notes.append(f"{draft.field} was not observed in the cluster")
        elif current != seen_value:
            notes.append(f"current value {draft.current!r} differs from the observed {seen!r}")
        if proposed is None:
            notes.append(f"proposed value {draft.proposed!r} is not a valid quantity")
        elif seen_value is not None:
            if proposed <= seen_value:
                notes.append("proposed value is not an increase")
            elif proposed > seen_value * max_factor:
                notes.append(f"proposed value exceeds {max_factor}x the current value")

    if notes:
        return (
            Proposal(
                action="investigate_further",
                rationale="The model's proposal failed validation: " + "; ".join(notes),
                risk="",
            ),
            notes,
        )
    return Proposal(**draft.model_dump()), notes
