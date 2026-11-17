"""Strands tools over ``ReadOnlyKube`` (REQ-K8S-01).

``build_tools`` takes the client by injection so tests pass a fake. Every tool returns
text wrapped in ``<tool_data>`` and never raises on an API error (REQ-K8S-05).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Protocol

from strands import tool

from .client import KubeError
from .redact import (
    bound_text,
    summarize_event,
    summarize_pod,
    summarize_template,
    to_tool_data,
)

DEFAULT_TAIL_LINES = 200
MAX_TAIL_LINES = 1000
MAX_LOG_BYTES = 200_000

_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
_KIND = re.compile(r"^[A-Z][A-Za-z]{0,62}$")


class KubeReader(Protocol):
    def get_pod(self, namespace: str, name: str) -> dict[str, Any]: ...
    def list_pods(self, namespace: str, label_selector: str = "") -> dict[str, Any]: ...
    def read_logs(self, namespace: str, name: str, container: str, *, previous: bool,
                  tail_lines: int, limit_bytes: int) -> str: ...
    def list_events(self, namespace: str, field_selector: str) -> dict[str, Any]: ...
    def get_workload(self, kind: str, namespace: str, name: str) -> dict[str, Any]: ...
    def list_resource_quotas(self, namespace: str) -> dict[str, Any]: ...
    def list_limit_ranges(self, namespace: str) -> dict[str, Any]: ...
    def list_hpas(self, namespace: str) -> dict[str, Any]: ...
    def get_node(self, name: str) -> dict[str, Any]: ...
    def get_pod_metrics(self, namespace: str, name: str) -> dict[str, Any]: ...


def _run(tool_name: str, fn: Callable[[], Any]) -> str:
    try:
        payload = fn()
    except KubeError as exc:
        payload = exc.as_result()
    return to_tool_data(tool_name, payload)


def _invalid(*values: tuple[str, re.Pattern[str]]) -> dict[str, Any] | None:
    for value, pattern in values:
        if not pattern.match(value or ""):
            return {"error": "invalid_argument", "detail": f"invalid name {value!r}"}
    return None


def resolve_owner(kube: KubeReader, namespace: str, pod: dict[str, Any]) -> dict[str, Any] | None:
    """Follow ownerReferences from a pod to its top-level workload."""
    owners = (pod.get("metadata") or {}).get("ownerReferences") or []
    if not owners:
        return None
    kind, name = owners[0].get("kind"), owners[0].get("name")
    workload = kube.get_workload(kind, namespace, name)
    if kind == "ReplicaSet":
        rs_owners = (workload.get("metadata") or {}).get("ownerReferences") or []
        if rs_owners and rs_owners[0].get("kind") == "Deployment":
            workload = kube.get_workload("Deployment", namespace, rs_owners[0]["name"])
    workload.setdefault("kind", kind)
    return workload


def build_tools(kube: KubeReader) -> list[Any]:
    @tool
    def get_pod(namespace: str, name: str) -> str:
        """Read one pod: container resources, env var names (never values), container
        states and last termination (reason, exit code), restart counts, owner and node."""
        return _run("get_pod", lambda: summarize_pod(kube.get_pod(namespace, name)))

    @tool
    def list_pods(namespace: str, label_selector: str = "") -> str:
        """List pods in a namespace, optionally filtered by a label selector."""

        def run() -> Any:
            items = kube.list_pods(namespace, label_selector).get("items") or []
            return [summarize_pod(p) for p in items[:50]]

        return _run("list_pods", run)

    @tool
    def get_pod_logs(
        namespace: str, name: str, container: str, previous: bool = True,
        tail_lines: int = DEFAULT_TAIL_LINES,
    ) -> str:
        """Read the tail of a container's log. previous=True reads the container instance
        that terminated last, which is where an OOMKilled process logged before dying."""
        lines = max(1, min(int(tail_lines), MAX_TAIL_LINES))
        return _run(
            "get_pod_logs",
            lambda: bound_text(
                kube.read_logs(namespace, name, container, previous=previous, tail_lines=lines,
                               limit_bytes=MAX_LOG_BYTES)
            ),
        )

    @tool
    def list_events(namespace: str, kind: str, name: str) -> str:
        """List recent events for one object, such as kind=Pod and the pod name."""
        bad = _invalid((kind, _KIND), (name, _NAME))
        if bad:
            return to_tool_data("list_events", bad)
        selector = f"involvedObject.kind={kind},involvedObject.name={name}"
        return _run(
            "list_events",
            lambda: [
                summarize_event(e)
                for e in (kube.list_events(namespace, selector).get("items") or [])[-30:]
            ],
        )

    @tool
    def get_owner_workload(namespace: str, pod_name: str) -> str:
        """Resolve a pod to the workload that owns it (for example its Deployment) and
        return that workload's replicas and container resource settings."""

        def run() -> Any:
            workload = resolve_owner(kube, namespace, kube.get_pod(namespace, pod_name))
            return {"owner": None} if workload is None else summarize_template(workload)

        return _run("get_owner_workload", run)

    @tool
    def get_quota_and_limits(namespace: str) -> str:
        """Read the namespace's ResourceQuotas and LimitRanges."""

        def run() -> Any:
            quotas = kube.list_resource_quotas(namespace).get("items") or []
            limits = kube.list_limit_ranges(namespace).get("items") or []
            return {
                "resourceQuotas": [
                    {"name": (q.get("metadata") or {}).get("name"), "status": q.get("status")}
                    for q in quotas
                ],
                "limitRanges": [
                    {"name": (lr.get("metadata") or {}).get("name"), "spec": lr.get("spec")}
                    for lr in limits
                ],
            }

        return _run("get_quota_and_limits", run)

    @tool
    def get_hpa(namespace: str, workload_name: str) -> str:
        """Read the HorizontalPodAutoscaler that targets a workload, if any."""

        def run() -> Any:
            hpas = kube.list_hpas(namespace).get("items") or []
            match = [
                h for h in hpas
                if ((h.get("spec") or {}).get("scaleTargetRef") or {}).get("name") == workload_name
            ]
            if not match:
                return {"hpa": None}
            h = match[0]
            return {"name": h["metadata"]["name"], "spec": h.get("spec"), "status": h.get("status")}

        return _run("get_hpa", run)

    @tool
    def get_node(name: str) -> str:
        """Read a node's conditions, allocatable and capacity."""

        def run() -> Any:
            node = kube.get_node(name)
            status = node.get("status") or {}
            return {
                "name": name,
                "conditions": [
                    {k: c.get(k) for k in ("type", "status", "reason")}
                    for c in status.get("conditions") or []
                ],
                "allocatable": status.get("allocatable"),
                "capacity": status.get("capacity"),
            }

        return _run("get_node", run)

    @tool
    def get_pod_metrics(namespace: str, name: str) -> str:
        """Read current CPU and memory usage of a pod's containers from metrics.k8s.io."""

        def run() -> Any:
            try:
                metrics = kube.get_pod_metrics(namespace, name)
            except KubeError as exc:
                return {"error": "metrics_unavailable", "detail": exc.reason}
            return {"containers": metrics.get("containers"), "window": metrics.get("window")}

        return _run("get_pod_metrics", run)

    return [
        get_pod, list_pods, get_pod_logs, list_events, get_owner_workload,
        get_quota_and_limits, get_hpa, get_node, get_pod_metrics,
    ]
