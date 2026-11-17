"""Turn API objects into small, safe summaries for the model (REQ-K8S-03, K8S-04).

Environment variables are returned by name only. Values, ``valueFrom`` targets and
``envFrom`` sources are dropped, and Secrets are never read.
"""

from __future__ import annotations

import json
from typing import Any

MAX_RESULT_CHARS = 8_000
MAX_TEXT_FIELD = 300


def _short(value: Any, limit: int = MAX_TEXT_FIELD) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "...[truncated]"
    return value


def _state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not state:
        return None
    for phase in ("terminated", "waiting", "running"):
        detail = state.get(phase)
        if detail is not None:
            keep = {k: detail.get(k) for k in ("reason", "exitCode", "startedAt", "finishedAt")}
            keep["message"] = _short(detail.get("message"))
            return {"phase": phase, **{k: v for k, v in keep.items() if v is not None}}
    return None


def summarize_container(container: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": container.get("name"),
        "image": container.get("image"),
        "resources": container.get("resources") or {},
        "env": [item.get("name") for item in container.get("env") or []],
        "env_from_count": len(container.get("envFrom") or []),
    }


def summarize_pod(pod: dict[str, Any]) -> dict[str, Any]:
    meta = pod.get("metadata") or {}
    spec = pod.get("spec") or {}
    status = pod.get("status") or {}
    return {
        "name": meta.get("name"),
        "namespace": meta.get("namespace"),
        "labels": meta.get("labels") or {},
        "owners": [
            {"kind": o.get("kind"), "name": o.get("name")}
            for o in meta.get("ownerReferences") or []
        ],
        "node": spec.get("nodeName"),
        "phase": status.get("phase"),
        "qosClass": status.get("qosClass"),
        "containers": [summarize_container(c) for c in spec.get("containers") or []],
        "containerStatuses": [
            {
                "name": cs.get("name"),
                "ready": cs.get("ready"),
                "restartCount": cs.get("restartCount"),
                "state": _state(cs.get("state")),
                "lastState": _state(cs.get("lastState")),
            }
            for cs in status.get("containerStatuses") or []
        ],
    }


def summarize_template(workload: dict[str, Any]) -> dict[str, Any]:
    meta = workload.get("metadata") or {}
    spec = workload.get("spec") or {}
    template_spec = (spec.get("template") or {}).get("spec") or {}
    return {
        "kind": workload.get("kind"),
        "name": meta.get("name"),
        "replicas": spec.get("replicas"),
        "containers": [summarize_container(c) for c in template_spec.get("containers") or []],
    }


def summarize_event(event: dict[str, Any]) -> dict[str, Any]:
    obj = event.get("involvedObject") or {}
    return {
        "type": event.get("type"),
        "reason": event.get("reason"),
        "message": _short(event.get("message")),
        "count": event.get("count"),
        "last": event.get("lastTimestamp") or event.get("eventTime"),
        "object": f"{obj.get('kind')}/{obj.get('name')}",
    }


def bound_text(text: str, max_chars: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[truncated {len(text) - max_chars} characters]"


def to_tool_data(tool_name: str, payload: Any, max_chars: int = MAX_RESULT_CHARS) -> str:
    """Wrap a tool result in delimiters the prompt marks as data (REQ-CPU-10).

    A closing delimiter inside the payload is escaped so cluster text cannot end the
    data block early.
    """
    body = payload if isinstance(payload, str) else json.dumps(payload, default=str, sort_keys=True)
    body = bound_text(body, max_chars).replace("</tool_data", "<\\/tool_data")
    return f'<tool_data tool="{tool_name}">\n{body}\n</tool_data>'
