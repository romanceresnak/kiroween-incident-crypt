"""A scripted Strands model for tests. It never makes a network call.

Each call to ``stream`` replays the next scripted turn as Bedrock Converse stream
events, so the real Strands event loop, tool execution and structured output run
unchanged.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

from strands.models.model import Model


@dataclass
class Text:
    text: str


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any] = field(default_factory=dict)


Turn = list[Text | ToolCall]
TurnFactory = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None], Turn]


def text(value: str) -> Turn:
    return [Text(value)]


def tool_call(tool_name: str, /, **arguments: Any) -> Turn:
    return [ToolCall(tool_name, arguments)]


class ScriptedModel(Model):
    def __init__(
        self,
        turns: list[Turn | TurnFactory],
        *,
        model_id: str = "eu.test.scripted-v1",
        input_tokens: int = 100,
        output_tokens: int = 20,
    ) -> None:
        self._turns = list(turns)
        self._config: dict[str, Any] = {"model_id": model_id}
        self._ids = itertools.count(1)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.calls = 0
        self.tool_specs_seen: list[list[str]] = []

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return self._config

    async def structured_output(  # type: ignore[override]
        self, output_model: Any, prompt: Any, system_prompt: str | None = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError("structured output goes through the tool path")
        yield {}  # pragma: no cover - makes this an async generator

    async def stream(  # type: ignore[override]
        self,
        messages: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.calls += 1
        self.tool_specs_seen.append([spec["name"] for spec in tool_specs or []])
        entry = self._turns.pop(0) if self._turns else text("done")
        turn = entry(messages, tool_specs) if callable(entry) else entry

        yield {"messageStart": {"role": "assistant"}}
        stop_reason = "end_turn"
        for block in turn:
            if isinstance(block, Text):
                yield {"contentBlockDelta": {"delta": {"text": block.text}}}
                yield {"contentBlockStop": {}}
            else:
                tool_use_id = f"tooluse_{next(self._ids)}"
                yield {
                    "contentBlockStart": {
                        "start": {"toolUse": {"toolUseId": tool_use_id, "name": block.name}}
                    }
                }
                tool_input = json.dumps(block.input)
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": tool_input}}}}
                yield {"contentBlockStop": {}}
                stop_reason = "tool_use"
        yield {"messageStop": {"stopReason": stop_reason}}
        yield {
            "metadata": {
                "usage": {
                    "inputTokens": self.input_tokens,
                    "outputTokens": self.output_tokens,
                    "totalTokens": self.input_tokens + self.output_tokens,
                },
                "metrics": {"latencyMs": 1},
            }
        }


# --- Kubernetes fakes -------------------------------------------------------


class FakeKube:
    """In-memory stand-in for ``ReadOnlyKube`` with the same read methods."""

    def __init__(
        self,
        *,
        pods: dict[tuple[str, str], dict[str, Any]] | None = None,
        workloads: dict[tuple[str, str, str], dict[str, Any]] | None = None,
        logs: dict[tuple[str, str, str, bool], str] | None = None,
        events: list[dict[str, Any]] | None = None,
        hpas: list[dict[str, Any]] | None = None,
        metrics: dict[tuple[str, str], dict[str, Any]] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.pods = pods or {}
        self.workloads = workloads or {}
        self.logs = logs or {}
        self.events = events or []
        self.hpas = hpas or []
        self.metrics = metrics or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, Any]] = []

    def _maybe_fail(self, method: str, *args: Any) -> None:
        self.calls.append((method, args))
        if method in self.errors:
            raise self.errors[method]

    def _not_found(self, what: str) -> Exception:
        from .k8s.client import KubeError

        return KubeError(404, "not_found", what)

    def get_pod(self, namespace: str, name: str) -> dict[str, Any]:
        self._maybe_fail("get_pod", namespace, name)
        if (namespace, name) not in self.pods:
            raise self._not_found(f"pod {namespace}/{name}")
        return self.pods[(namespace, name)]

    def list_pods(self, namespace: str, label_selector: str = "") -> dict[str, Any]:
        self._maybe_fail("list_pods", namespace, label_selector)
        return {"items": [p for (ns, _), p in self.pods.items() if ns == namespace]}

    def read_logs(self, namespace: str, name: str, container: str, *, previous: bool,
                  tail_lines: int, limit_bytes: int) -> str:
        self._maybe_fail("read_logs", namespace, name, container, previous, tail_lines)
        text = self.logs.get((namespace, name, container, previous))
        if text is None:
            raise self._not_found("log")
        return "\n".join(text.splitlines()[-tail_lines:])

    def list_events(self, namespace: str, field_selector: str) -> dict[str, Any]:
        self._maybe_fail("list_events", namespace, field_selector)
        return {"items": self.events}

    def get_workload(self, kind: str, namespace: str, name: str) -> dict[str, Any]:
        self._maybe_fail("get_workload", kind, namespace, name)
        if (kind, namespace, name) not in self.workloads:
            raise self._not_found(f"{kind} {namespace}/{name}")
        return self.workloads[(kind, namespace, name)]

    def list_resource_quotas(self, namespace: str) -> dict[str, Any]:
        self._maybe_fail("list_resource_quotas", namespace)
        return {"items": []}

    def list_limit_ranges(self, namespace: str) -> dict[str, Any]:
        self._maybe_fail("list_limit_ranges", namespace)
        return {"items": []}

    def list_hpas(self, namespace: str) -> dict[str, Any]:
        self._maybe_fail("list_hpas", namespace)
        return {"items": self.hpas}

    def get_node(self, name: str) -> dict[str, Any]:
        self._maybe_fail("get_node", name)
        return {"status": {"conditions": [{"type": "Ready", "status": "True"}],
                           "allocatable": {"memory": "7Gi", "cpu": "1930m"}}}

    def get_pod_metrics(self, namespace: str, name: str) -> dict[str, Any]:
        self._maybe_fail("get_pod_metrics", namespace, name)
        if (namespace, name) not in self.metrics:
            raise self._not_found("pods.metrics.k8s.io")
        return self.metrics[(namespace, name)]


OOM_NAMESPACE = "chaos-oom"
OOM_DEPLOYMENT = "oom-demo"
OOM_REPLICASET = "oom-demo-7d9f8c6b5"
OOM_POD = "oom-demo-7d9f8c6b5-x2k4q"
OOM_CONTAINER = "app"
# A marker placed in env values; tests assert it never appears in tool output.
LEAK_CANARY = "canary-value-must-not-appear-in-output"


def _container(limit: str = "64Mi", request: str = "32Mi") -> dict[str, Any]:
    return {
        "name": OOM_CONTAINER,
        "image": "public.ecr.aws/docker/library/python:3.12-slim",
        "resources": {"limits": {"memory": limit}, "requests": {"memory": request}},
        "env": [
            {"name": "PLAIN", "value": LEAK_CANARY},
            {"name": "FROM_SECRET",
             "valueFrom": {"secretKeyRef": {"name": "db-creds", "key": "password"}}},
        ],
        "envFrom": [{"secretRef": {"name": "all-the-secrets"}}],
    }


def oom_pod(*, finished_at: str = "2026-11-17T13:58:00Z", limit: str = "64Mi") -> dict[str, Any]:
    """A pod whose container was OOMKilled, owned by a ReplicaSet of a Deployment."""
    return {
        "kind": "Pod",
        "metadata": {
            "name": OOM_POD,
            "namespace": OOM_NAMESPACE,
            "labels": {"app": OOM_DEPLOYMENT, "pod-template-hash": "7d9f8c6b5"},
            "ownerReferences": [{"kind": "ReplicaSet", "name": OOM_REPLICASET}],
            "resourceVersion": "1000",
        },
        "spec": {"nodeName": "ip-10-0-18-35", "containers": [_container(limit)]},
        "status": {
            "phase": "Running",
            "qosClass": "Burstable",
            "containerStatuses": [{
                "name": OOM_CONTAINER,
                "ready": False,
                "restartCount": 4,
                "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                "lastState": {"terminated": {
                    "reason": "OOMKilled", "exitCode": 137,
                    "startedAt": "2026-11-17T13:57:40Z", "finishedAt": finished_at,
                }},
            }],
        },
    }


def oom_kube(**overrides: Any) -> FakeKube:
    pod = oom_pod()
    deployment = {
        "kind": "Deployment",
        "metadata": {"name": OOM_DEPLOYMENT, "namespace": OOM_NAMESPACE},
        "spec": {"replicas": 1, "template": {"spec": {"containers": [_container()]}}},
    }
    replicaset = {
        "kind": "ReplicaSet",
        "metadata": {"name": OOM_REPLICASET, "namespace": OOM_NAMESPACE,
                     "ownerReferences": [{"kind": "Deployment", "name": OOM_DEPLOYMENT}]},
        "spec": {"template": {"spec": {"containers": [_container()]}}},
    }
    kwargs: dict[str, Any] = {
        "pods": {(OOM_NAMESPACE, OOM_POD): pod},
        "workloads": {
            ("ReplicaSet", OOM_NAMESPACE, OOM_REPLICASET): replicaset,
            ("Deployment", OOM_NAMESPACE, OOM_DEPLOYMENT): deployment,
        },
        "logs": {(OOM_NAMESPACE, OOM_POD, OOM_CONTAINER, True):
                 "allocated 48 MiB\nallocated 56 MiB\nallocated 64 MiB\n"},
        "events": [{"type": "Warning", "reason": "BackOff", "message": "Back-off restarting",
                    "count": 4, "involvedObject": {"kind": "Pod", "name": OOM_POD}}],
    }
    kwargs.update(overrides)
    return FakeKube(**kwargs)


# --- generic in-memory cluster for the specialists --------------------------


class FakeCluster:
    """In-memory cluster with every ``ReadOnlyKube`` read method.

    Objects are keyed by (kind, namespace, name); cluster-scoped objects use namespace "".
    """

    CLUSTER_SCOPED = frozenset({"Node", "PersistentVolume", "StorageClass", "VolumeAttachment"})

    def __init__(
        self,
        objects: list[dict[str, Any]] | None = None,
        *,
        logs: dict[tuple[str, str, str], str] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.objects: dict[tuple[str, str, str], dict[str, Any]] = {}
        for obj in objects or []:
            self.add(obj)
        self.logs = logs or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, Any]] = []

    def add(self, obj: dict[str, Any]) -> None:
        meta = obj["metadata"]
        ns = "" if obj["kind"] in self.CLUSTER_SCOPED else meta.get("namespace", "")
        self.objects[(obj["kind"], ns, meta["name"])] = obj

    def _record(self, method: str, *args: Any) -> None:
        self.calls.append((method, args))
        if method in self.errors:
            raise self.errors[method]

    def _get(self, kind: str, ns: str, name: str) -> dict[str, Any]:
        from .k8s.client import KubeError

        if (kind, ns, name) not in self.objects:
            raise KubeError(404, "not_found", f"{kind} {ns}/{name}")
        return self.objects[(kind, ns, name)]

    def _list(self, kind: str, ns: str | None = None, selector: str = "") -> dict[str, Any]:
        wanted = dict(part.split("=", 1) for part in selector.split(",") if part)
        items = []
        for (k, n, _), obj in self.objects.items():
            if k != kind or (ns is not None and n != ns):
                continue
            labels = obj["metadata"].get("labels") or {}
            if all(labels.get(key) == value for key, value in wanted.items()):
                items.append(obj)
        return {"items": items}

    # core
    def get_pod(self, namespace: str, name: str) -> dict[str, Any]:
        self._record("get_pod", namespace, name)
        return self._get("Pod", namespace, name)

    def list_pods(self, namespace: str, label_selector: str = "") -> dict[str, Any]:
        self._record("list_pods", namespace, label_selector)
        return self._list("Pod", namespace, label_selector)

    def read_logs(self, namespace: str, name: str, container: str, *, previous: bool,
                  tail_lines: int, limit_bytes: int) -> str:
        from .k8s.client import KubeError

        self._record("read_logs", namespace, name, container, previous, tail_lines)
        if (namespace, name, container) not in self.logs:
            raise KubeError(404, "not_found", "log")
        return "\n".join(self.logs[(namespace, name, container)].splitlines()[-tail_lines:])

    def list_events(self, namespace: str, field_selector: str) -> dict[str, Any]:
        self._record("list_events", namespace, field_selector)
        wanted = dict(p.split("=", 1) for p in field_selector.split(",") if p)
        items = [e for e in self._list("Event", namespace)["items"]
                 if all((e.get("involvedObject") or {}).get(k.split(".", 1)[1]) == v
                        for k, v in wanted.items())]
        return {"items": items}

    def get_workload(self, kind: str, namespace: str, name: str) -> dict[str, Any]:
        self._record("get_workload", kind, namespace, name)
        return self._get(kind, namespace, name)

    def list_resource_quotas(self, namespace: str) -> dict[str, Any]:
        return self._list("ResourceQuota", namespace)

    def list_limit_ranges(self, namespace: str) -> dict[str, Any]:
        return self._list("LimitRange", namespace)

    def list_hpas(self, namespace: str) -> dict[str, Any]:
        return self._list("HorizontalPodAutoscaler", namespace)

    def get_node(self, name: str) -> dict[str, Any]:
        self._record("get_node", name)
        return self._get("Node", "", name)

    def get_pod_metrics(self, namespace: str, name: str) -> dict[str, Any]:
        return self._get("PodMetrics", namespace, name)

    # network
    def get_service(self, namespace: str, name: str) -> dict[str, Any]:
        self._record("get_service", namespace, name)
        return self._get("Service", namespace, name)

    def list_services(self, namespace: str) -> dict[str, Any]:
        return self._list("Service", namespace)

    def list_endpoint_slices(self, namespace: str, service: str) -> dict[str, Any]:
        return self._list("EndpointSlice", namespace, f"kubernetes.io/service-name={service}")

    def get_network_policy(self, namespace: str, name: str) -> dict[str, Any]:
        self._record("get_network_policy", namespace, name)
        return self._get("NetworkPolicy", namespace, name)

    def list_network_policies(self, namespace: str) -> dict[str, Any]:
        return self._list("NetworkPolicy", namespace)

    def list_ingresses(self, namespace: str) -> dict[str, Any]:
        return self._list("Ingress", namespace)

    # deploy-rollout
    def get_configmap(self, namespace: str, name: str) -> dict[str, Any]:
        self._record("get_configmap", namespace, name)
        return self._get("ConfigMap", namespace, name)

    def list_replica_sets(self, namespace: str, label_selector: str) -> dict[str, Any]:
        return self._list("ReplicaSet", namespace, label_selector)

    def get_application(self, namespace: str, name: str) -> dict[str, Any]:
        return self._get("Application", namespace, name)

    # storage
    def get_pvc(self, namespace: str, name: str) -> dict[str, Any]:
        self._record("get_pvc", namespace, name)
        return self._get("PersistentVolumeClaim", namespace, name)

    def list_pvcs(self, namespace: str) -> dict[str, Any]:
        return self._list("PersistentVolumeClaim", namespace)

    def get_pv(self, name: str) -> dict[str, Any]:
        return self._get("PersistentVolume", "", name)

    def list_storage_classes(self) -> dict[str, Any]:
        return self._list("StorageClass")

    def list_volume_attachments(self) -> dict[str, Any]:
        return self._list("VolumeAttachment")


def obj(kind: str, name: str, namespace: str = "", **fields: Any) -> dict[str, Any]:
    """Build a minimal API object for FakeCluster."""
    metadata = {"name": name, **({"namespace": namespace} if namespace else {})}
    metadata.update(fields.pop("metadata", {}))
    return {"kind": kind, "metadata": metadata, **fields}
