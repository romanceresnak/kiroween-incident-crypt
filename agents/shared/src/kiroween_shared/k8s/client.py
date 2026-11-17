"""The only module that talks to the Kubernetes API. Read verbs only (REQ-K8S-02).

Every method maps to a ``read_*``, ``list_*`` or ``get_*`` call. A test scans this
package and fails on any call that could change the cluster.
"""

from __future__ import annotations

from typing import Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException

_REASONS = {401: "unauthorized", 403: "forbidden", 404: "not_found", 410: "gone"}


class KubeError(Exception):
    """A Kubernetes API error, reported to the agent instead of raised (REQ-K8S-05)."""

    def __init__(self, status: int, reason: str, detail: str = "") -> None:
        super().__init__(f"{status} {reason}: {detail}")
        self.status = status
        self.reason = reason
        self.detail = detail

    def as_result(self) -> dict[str, Any]:
        return {"error": self.reason, "status": self.status, "detail": self.detail[:300]}


def load_api_client() -> client.ApiClient:
    try:
        config.load_incluster_config()
    except ConfigException:
        config.load_kube_config()
    return client.ApiClient()


class ReadOnlyKube:
    def __init__(self, api_client: client.ApiClient | None = None) -> None:
        self._api = api_client or load_api_client()
        self._core = client.CoreV1Api(self._api)
        self._apps = client.AppsV1Api(self._api)
        self._autoscaling = client.AutoscalingV2Api(self._api)
        self._custom = client.CustomObjectsApi(self._api)

    def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            result = fn(*args, **kwargs)
        except ApiException as exc:
            status = exc.status or 0
            raise KubeError(status, _REASONS.get(status, "api_error"), str(exc.reason)) from exc
        if isinstance(result, str | dict):
            return result
        return self._api.sanitize_for_serialization(result)

    def get_pod(self, namespace: str, name: str) -> dict[str, Any]:
        return self._call(self._core.read_namespaced_pod, name, namespace)

    def list_pods(self, namespace: str, label_selector: str = "") -> dict[str, Any]:
        return self._call(
            self._core.list_namespaced_pod, namespace, label_selector=label_selector, limit=50
        )

    def read_logs(
        self, namespace: str, name: str, container: str, *, previous: bool, tail_lines: int,
        limit_bytes: int,
    ) -> str:
        return self._call(
            self._core.read_namespaced_pod_log,
            name,
            namespace,
            container=container,
            previous=previous,
            tail_lines=tail_lines,
            limit_bytes=limit_bytes,
        )

    def list_events(self, namespace: str, field_selector: str) -> dict[str, Any]:
        return self._call(
            self._core.list_namespaced_event, namespace, field_selector=field_selector, limit=50
        )

    def get_workload(self, kind: str, namespace: str, name: str) -> dict[str, Any]:
        readers = {
            "Deployment": self._apps.read_namespaced_deployment,
            "ReplicaSet": self._apps.read_namespaced_replica_set,
            "StatefulSet": self._apps.read_namespaced_stateful_set,
            "DaemonSet": self._apps.read_namespaced_daemon_set,
        }
        reader = readers.get(kind)
        if reader is None:
            raise KubeError(400, "unsupported_kind", kind)
        return self._call(reader, name, namespace)

    def list_resource_quotas(self, namespace: str) -> dict[str, Any]:
        return self._call(self._core.list_namespaced_resource_quota, namespace)

    def list_limit_ranges(self, namespace: str) -> dict[str, Any]:
        return self._call(self._core.list_namespaced_limit_range, namespace)

    def list_hpas(self, namespace: str) -> dict[str, Any]:
        return self._call(
            self._autoscaling.list_namespaced_horizontal_pod_autoscaler, namespace
        )

    def get_node(self, name: str) -> dict[str, Any]:
        return self._call(self._core.read_node, name)

    def get_pod_metrics(self, namespace: str, name: str) -> dict[str, Any]:
        return self._call(
            self._custom.get_namespaced_custom_object,
            "metrics.k8s.io", "v1beta1", namespace, "pods", name,
        )
