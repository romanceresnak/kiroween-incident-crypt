"""Code-validated ``set_field`` proposals for the specialists (specialists REQ-PRO).

A ``set_field`` proposal changes one field of one object. The model may propose it; code
decides whether it is returned. Every check here works on values read from the cluster
by code, not on what the model says it read.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .k8s.client import KubeError
from .models import Incident, Proposal, ProposalDraft

# Never proposed against, whatever the incident says (REQ-PRO-04).
PROTECTED_NAMESPACES = frozenset({"kube-system", "kube-public", "kube-node-lease", "argocd",
                                  "agents"})

LABEL_VALUE = re.compile(r"^[A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?$")
PORT_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]{0,13}[a-z0-9])?$")
# One label-key segment in a path: a plain key, or a quoted key for keys with dots or
# slashes, such as ["app.kubernetes.io/name"].
LABEL_KEY_SEGMENT = r'(\.[A-Za-z0-9_-]+|\["[A-Za-z0-9./_-]+"\])'

_TOKEN = re.compile(
    r'\.?(?P<key>[A-Za-z0-9_-]+)|\["(?P<qkey>[^"\]]+)"\]|\[(?P<idx>\d+)\]'
    r"|\[name=(?P<name>[A-Za-z0-9._-]+)\]"
)
MISSING: Any = object()


class PathError(ValueError):
    pass


def parse_path(path: str) -> list[str | int | tuple[str, str]]:
    """Parse ``a.b[0].c[name=x]["k.io/v"]`` into tokens."""
    if not path or path.startswith(".") or path.endswith("."):
        raise PathError(f"invalid field path {path!r}")
    tokens: list[str | int | tuple[str, str]] = []
    pos = 0
    while pos < len(path):
        match = _TOKEN.match(path, pos)
        if not match or match.end() == pos:
            raise PathError(f"invalid field path {path!r}")
        if match.group("key") is not None:
            if pos > 0 and path[pos] != ".":
                raise PathError(f"invalid field path {path!r}")
            tokens.append(match.group("key"))
        elif match.group("qkey") is not None:
            tokens.append(match.group("qkey"))
        elif match.group("idx") is not None:
            tokens.append(int(match.group("idx")))
        else:
            tokens.append(("name", match.group("name")))
        pos = match.end()
    return tokens


def resolve(obj: Any, tokens: list[str | int | tuple[str, str]]) -> Any:
    """The value at ``tokens``, or ``MISSING``."""
    current = obj
    for token in tokens:
        if isinstance(token, str):
            if not isinstance(current, dict) or token not in current:
                return MISSING
            current = current[token]
        elif isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return MISSING
            current = current[token]
        else:
            if not isinstance(current, list):
                return MISSING
            match = [i for i in current if isinstance(i, dict) and i.get("name") == token[1]]
            if not match:
                return MISSING
            current = match[0]
    return current


@dataclass(frozen=True)
class Context:
    incident: Incident
    kube: Any
    obj: dict[str, Any] | None = None


Check = Callable[[Context, ProposalDraft, Any], str | None]
TargetCheck = Callable[[Context, ProposalDraft], str | None]


@dataclass(frozen=True)
class ChangeRule:
    """One allowed change: a kind, a field pattern and a value check."""

    kind: str
    field: re.Pattern[str]
    check: Check
    target: TargetCheck | None = None


READERS: dict[str, Callable[[Any, str, str], dict[str, Any]]] = {
    "Service": lambda kube, ns, name: kube.get_service(ns, name),
    "NetworkPolicy": lambda kube, ns, name: kube.get_network_policy(ns, name),
    "Deployment": lambda kube, ns, name: kube.get_workload("Deployment", ns, name),
    "StatefulSet": lambda kube, ns, name: kube.get_workload("StatefulSet", ns, name),
    "DaemonSet": lambda kube, ns, name: kube.get_workload("DaemonSet", ns, name),
    "PersistentVolumeClaim": lambda kube, ns, name: kube.get_pvc(ns, name),
}


def _replacement(reason: str) -> Proposal:
    return Proposal(
        action="investigate_further",
        rationale=f"The model's proposal failed validation: {reason}",
        risk="",
    )


def validate_change(
    incident: Incident, draft: ProposalDraft, rules: list[ChangeRule], kube: Any
) -> tuple[Proposal, list[str]]:
    """Return the proposal to send and the reason it was replaced, if it was."""
    if draft.action in ("investigate_further", "none"):
        return Proposal(action=draft.action, rationale=draft.rationale, risk=draft.risk), []

    def reject(reason: str) -> tuple[Proposal, list[str]]:
        return _replacement(reason), [reason]

    if draft.action != "set_field":
        return reject(f"action {draft.action!r} is not allowed for this specialist")
    target = draft.target
    if target is None:
        return reject("set_field needs a target")
    if target.namespace != incident.namespace:
        return reject("target namespace is not the incident's namespace")
    if target.namespace in PROTECTED_NAMESPACES:
        return reject(f"namespace {target.namespace} is protected")
    field = draft.field or ""
    rule = next((r for r in rules if r.kind == target.kind and r.field.fullmatch(field)), None)
    if rule is None:
        return reject(f"changing {target.kind} {field!r} is not allowed")
    try:
        tokens = parse_path(field)
    except PathError as exc:
        return reject(str(exc))
    if rule.target is not None:
        error = rule.target(Context(incident, kube), draft)
        if error:
            return reject(error)
    reader = READERS.get(target.kind)
    if reader is None:
        return reject(f"{target.kind} cannot be read")
    try:
        obj = reader(kube, target.namespace, target.name)
    except KubeError as exc:
        return reject(f"{target.kind} {target.namespace}/{target.name} could not be read "
                      f"({exc.reason})")

    observed = resolve(obj, tokens)
    if observed is MISSING:
        if draft.current is not None:
            return reject(f"{field} is not set, but the proposal says it is {draft.current!r}")
    elif isinstance(observed, dict | list):
        return reject(f"{field} is not a single value")
    elif str(observed) != (draft.current if draft.current is not None else ""):
        return reject(f"current value {draft.current!r} differs from the observed {observed!r}")
    if draft.proposed is None or draft.proposed == draft.current:
        return reject("the proposal does not change the value")

    error = rule.check(Context(incident, kube, obj), draft,
                       None if observed is MISSING else observed)
    if error:
        return reject(error)
    return Proposal(**draft.model_dump()), []


# --- reusable value checks ---------------------------------------------------


def label_value(ctx: Context, draft: ProposalDraft, observed: Any) -> str | None:
    if not LABEL_VALUE.match(draft.proposed or ""):
        return f"{draft.proposed!r} is not a valid label value"
    return None


def port_value(ctx: Context, draft: ProposalDraft, observed: Any) -> str | None:
    value = draft.proposed or ""
    if value.isdigit():
        return None if 1 <= int(value) <= 65535 else f"port {value} is out of range"
    return None if PORT_NAME.match(value) else f"{value!r} is not a port number or name"


def incident_workload(ctx: Context, draft: ProposalDraft) -> str | None:
    target = draft.target
    workload = ctx.incident.workload
    if target is None or (target.kind, target.name) != (workload.kind, workload.name):
        return "target is not the incident's workload"
    return None
