"""The lab-runner's Kubernetes writer: the only module in the orchestrator image that creates
or deletes anything in a cluster (scenario-lab REQ-RN-06).

What it may call is fixed by the Role the chart gives the runner, and by a test that walks this
file. It also refuses, itself, anything that is not one of the five lab kinds in a ``lab-NN``
namespace, whatever it is asked.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from kubernetes.client.exceptions import ApiException

from .render import KINDS, LABEL_LAB, SELECTOR
from .scenarios import Obj

logger = logging.getLogger(__name__)

LAB_NAMESPACE = re.compile(r"lab-(0[1-9]|1[0-4])")
TIMEOUT_S = 15


class RefusedError(ValueError):
    """The writer was asked for something outside the lab."""


def check_namespace(namespace: str) -> str:
    if not LAB_NAMESPACE.fullmatch(namespace):
        raise RefusedError(f"{namespace!r} is not a lab namespace")
    return namespace


class KubernetesWriter:
    def __init__(self, core: Any, apps: Any, networking: Any) -> None:
        self._core, self._apps, self._net = core, apps, networking

    @classmethod
    def in_cluster(cls) -> KubernetesWriter:
        from kubernetes import client, config

        config.load_incluster_config()
        return cls(client.CoreV1Api(), client.AppsV1Api(), client.NetworkingV1Api())

    # --- reading ---------------------------------------------------------------------------

    def namespace_ready(self, namespace: str) -> bool:
        check_namespace(namespace)
        try:
            found = self._core.read_namespace(namespace, _request_timeout=TIMEOUT_S)
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise
        return (found.metadata.labels or {}).get(LABEL_LAB) == "true"

    def deployment_ready(self, namespace: str, name: str) -> bool:
        check_namespace(namespace)
        try:
            d = self._apps.read_namespaced_deployment(name, namespace,
                                                      _request_timeout=TIMEOUT_S)
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise
        wanted = d.spec.replicas if d.spec.replicas is not None else 1
        status = d.status
        return ((status.ready_replicas or 0) >= wanted and (status.updated_replicas or 0) >= wanted
                and (status.observed_generation or 0) >= (d.metadata.generation or 0))

    def is_empty(self, namespace: str) -> bool:
        check_namespace(namespace)
        kwargs = {"label_selector": SELECTOR, "_request_timeout": TIMEOUT_S}
        listings = (
            self._apps.list_namespaced_deployment(namespace, **kwargs),
            self._core.list_namespaced_pod(namespace, **kwargs),
            self._core.list_namespaced_service(namespace, **kwargs),
            self._core.list_namespaced_config_map(namespace, **kwargs),
            self._core.list_namespaced_persistent_volume_claim(namespace, **kwargs),
            self._net.list_namespaced_network_policy(namespace, **kwargs),
        )
        return not any(listing.items for listing in listings)

    # --- writing ---------------------------------------------------------------------------

    def apply(self, obj: Obj) -> None:
        """Create the object; a Deployment that exists is patched (phase two of a scenario)."""
        kind = obj.get("kind", "")
        meta = obj.get("metadata", {})
        namespace, name = check_namespace(meta.get("namespace", "")), meta.get("name", "")
        if kind not in KINDS or (meta.get("labels") or {}).get(LABEL_LAB) != "true":
            raise RefusedError(f"{kind}/{name} is not a lab object")
        try:
            self._create(kind, namespace, obj)
        except ApiException as exc:
            if exc.status == 409 and kind == "Deployment":
                self._apps.patch_namespaced_deployment(name, namespace, obj,
                                                       _request_timeout=TIMEOUT_S)
            else:
                raise

    def _create(self, kind: str, namespace: str, obj: Obj) -> None:
        kwargs = {"_request_timeout": TIMEOUT_S}
        if kind == "Deployment":
            self._apps.create_namespaced_deployment(namespace, obj, **kwargs)
        elif kind == "Service":
            self._core.create_namespaced_service(namespace, obj, **kwargs)
        elif kind == "ConfigMap":
            self._core.create_namespaced_config_map(namespace, obj, **kwargs)
        elif kind == "PersistentVolumeClaim":
            self._core.create_namespaced_persistent_volume_claim(namespace, obj, **kwargs)
        elif kind == "NetworkPolicy":
            self._net.create_namespaced_network_policy(namespace, obj, **kwargs)
        else:  # unreachable: apply() checked the kind
            raise RefusedError(kind)

    def delete_all(self, namespace: str) -> None:
        """Delete everything carrying the lab label. Services have no collection delete."""
        check_namespace(namespace)
        kwargs = {"label_selector": SELECTOR, "_request_timeout": TIMEOUT_S}
        self._apps.delete_collection_namespaced_deployment(
            namespace, propagation_policy="Background", **kwargs)
        self._core.delete_collection_namespaced_pod(namespace, **kwargs)
        for service in self._core.list_namespaced_service(namespace, **kwargs).items:
            try:
                self._core.delete_namespaced_service(service.metadata.name, namespace,
                                                     _request_timeout=TIMEOUT_S)
            except ApiException as exc:
                if exc.status != 404:
                    raise
        self._core.delete_collection_namespaced_config_map(namespace, **kwargs)
        self._core.delete_collection_namespaced_persistent_volume_claim(namespace, **kwargs)
        self._net.delete_collection_namespaced_network_policy(namespace, **kwargs)
