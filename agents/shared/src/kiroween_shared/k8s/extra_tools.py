"""Read-only tools for the network, deploy-rollout and storage specialists (REQ-TLS).

Same conventions as ``tools.py``: results are bounded ``<tool_data>`` blocks, API errors
become results, Secrets are never read, and ConfigMaps return key names only (except the
CoreDNS Corefile, which is configuration, not application data).
"""

from __future__ import annotations

from typing import Any

from strands import tool

from .client import KubeError
from .redact import bound_text, summarize_pod, summarize_template
from .tools import _run, build_tools

LOG_TAIL = 60


def select(tools: list[Any], names: set[str]) -> list[Any]:
    chosen = [t for t in tools if t.tool_name in names]
    missing = names - {t.tool_name for t in chosen}
    if missing:
        raise ValueError(f"unknown tools: {sorted(missing)}")
    return chosen


def _meta(obj: dict[str, Any]) -> dict[str, Any]:
    return obj.get("metadata") or {}


def _items(result: dict[str, Any], limit: int = 50) -> list[dict[str, Any]]:
    return (result.get("items") or [])[:limit]


def _logs(kube: Any, namespace: str, pod: str, container: str) -> str:
    try:
        return bound_text(kube.read_logs(namespace, pod, container, previous=False,
                                         tail_lines=LOG_TAIL, limit_bytes=50_000), 3_000)
    except KubeError as exc:
        return f"<logs unavailable: {exc.reason}>"


# --- network ---------------------------------------------------------------


def summarize_service(svc: dict[str, Any]) -> dict[str, Any]:
    spec = svc.get("spec") or {}
    return {
        "name": _meta(svc).get("name"),
        "type": spec.get("type"),
        "selector": spec.get("selector") or {},
        "ports": [{k: p.get(k) for k in ("name", "port", "targetPort", "protocol")}
                  for p in spec.get("ports") or []],
    }


def summarize_policy(policy: dict[str, Any]) -> dict[str, Any]:
    spec = policy.get("spec") or {}
    return {
        "name": _meta(policy).get("name"),
        "podSelector": spec.get("podSelector"),
        "policyTypes": spec.get("policyTypes"),
        "ingress": spec.get("ingress") or [],
        "egress": spec.get("egress") or [],
    }


def network_tools(kube: Any) -> list[Any]:
    @tool
    def get_service(namespace: str, name: str) -> str:
        """Read a Service: type, selector and ports (port, targetPort, protocol)."""
        return _run("get_service", lambda: summarize_service(kube.get_service(namespace, name)))

    @tool
    def list_services(namespace: str) -> str:
        """List the Services in a namespace with their selectors and ports."""
        return _run("list_services", lambda: [
            summarize_service(s) for s in _items(kube.list_services(namespace))])

    @tool
    def get_endpoints(namespace: str, service: str) -> str:
        """Read the EndpointSlices of a Service: which pod addresses are ready, which are
        not, and the ports. No ready address means the selector matches no ready pod."""

        def run() -> Any:
            slices = _items(kube.list_endpoint_slices(namespace, service))
            endpoints = [
                {"address": (e.get("addresses") or [None])[0],
                 "ready": (e.get("conditions") or {}).get("ready"),
                 "pod": (e.get("targetRef") or {}).get("name")}
                for s in slices for e in s.get("endpoints") or []
            ]
            return {
                "service": service,
                "slices": len(slices),
                "ready": sum(1 for e in endpoints if e["ready"]),
                "not_ready": sum(1 for e in endpoints if not e["ready"]),
                "endpoints": endpoints[:30],
                "ports": [p for s in slices for p in s.get("ports") or []][:10],
            }

        return _run("get_endpoints", run)

    @tool
    def list_network_policies(namespace: str) -> str:
        """List the NetworkPolicies in a namespace: which pods each selects, and which
        sources and ports its ingress and egress rules allow."""
        return _run("list_network_policies", lambda: [
            summarize_policy(p) for p in _items(kube.list_network_policies(namespace))])

    @tool
    def list_ingresses(namespace: str) -> str:
        """List the Ingresses in a namespace: class, rules, backends and load balancer status."""

        def run() -> Any:
            return [
                {"name": _meta(i).get("name"),
                 "class": (i.get("spec") or {}).get("ingressClassName"),
                 "rules": (i.get("spec") or {}).get("rules") or [],
                 "tls": [t.get("hosts") for t in (i.get("spec") or {}).get("tls") or []],
                 "loadBalancer": ((i.get("status") or {}).get("loadBalancer") or {})
                                 .get("ingress")}
                for i in _items(kube.list_ingresses(namespace))
            ]

        return _run("list_ingresses", run)

    @tool
    def get_coredns_status() -> str:
        """Read cluster DNS health: CoreDNS pod states, recent CoreDNS log lines, and the
        Corefile."""

        def run() -> Any:
            pods = _items(kube.list_pods("kube-system", "k8s-app=kube-dns"), 10)
            summary = [summarize_pod(p) for p in pods]
            logs = _logs(kube, "kube-system", _meta(pods[0]).get("name", ""), "coredns") \
                if pods else "<no CoreDNS pods>"
            try:
                corefile = (kube.get_configmap("kube-system", "coredns").get("data") or {}) \
                    .get("Corefile")
            except KubeError as exc:
                corefile = f"<unavailable: {exc.reason}>"
            return {"pods": summary, "recent_logs": logs, "corefile": corefile}

        return _run("get_coredns_status", run)

    base = select(build_tools(kube), {"get_pod", "list_pods", "get_pod_logs", "list_events"})
    return base + [get_service, list_services, get_endpoints, list_network_policies,
                   list_ingresses, get_coredns_status]


# --- deploy-rollout ------------------------------------------------------------


def rollout_history(kube: Any, namespace: str, name: str) -> list[dict[str, Any]]:
    """ReplicaSets owned by a Deployment, newest revision first."""
    deployment = kube.get_workload("Deployment", namespace, name)
    labels = ((deployment.get("spec") or {}).get("selector") or {}).get("matchLabels") or {}
    selector = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
    history = []
    for rs in _items(kube.list_replica_sets(namespace, selector), 100):
        owners = _meta(rs).get("ownerReferences") or []
        if not any(o.get("kind") == "Deployment" and o.get("name") == name for o in owners):
            continue
        revision = (_meta(rs).get("annotations") or {}).get("deployment.kubernetes.io/revision")
        containers = (((rs.get("spec") or {}).get("template") or {}).get("spec") or {}) \
            .get("containers") or []
        history.append({
            "replicaset": _meta(rs).get("name"),
            "revision": int(revision) if (revision or "").isdigit() else None,
            "images": {c.get("name"): c.get("image") for c in containers},
            "replicas": (rs.get("spec") or {}).get("replicas"),
            "ready": (rs.get("status") or {}).get("readyReplicas") or 0,
        })
    return sorted(history, key=lambda h: h["revision"] or 0, reverse=True)


def deploy_tools(kube: Any) -> list[Any]:
    @tool
    def get_rollout_history(namespace: str, deployment: str) -> str:
        """List a Deployment's ReplicaSets, newest first: revision, container images, and
        ready replicas. An earlier revision that was ready is a known-good image."""
        return _run("get_rollout_history", lambda: rollout_history(kube, namespace, deployment))

    @tool
    def get_workload_status(namespace: str, kind: str, name: str) -> str:
        """Read a Deployment, StatefulSet or DaemonSet: conditions (Progressing, Available,
        ReplicaFailure), generation, replica counts, and the pod template's containers."""

        def run() -> Any:
            workload = kube.get_workload(kind, namespace, name)
            status = workload.get("status") or {}
            summary = summarize_template({**workload, "kind": kind})
            summary.update({
                "generation": _meta(workload).get("generation"),
                "observedGeneration": status.get("observedGeneration"),
                "status": {k: status.get(k) for k in (
                    "replicas", "updatedReplicas", "readyReplicas", "availableReplicas",
                    "unavailableReplicas")},
                "conditions": [{k: c.get(k) for k in ("type", "status", "reason", "message")}
                               for c in status.get("conditions") or []],
                "progressDeadlineSeconds": (workload.get("spec") or {})
                .get("progressDeadlineSeconds"),
            })
            return summary

        return _run("get_workload_status", run)

    @tool
    def list_configmap_keys(namespace: str, name: str) -> str:
        """List the key names of a ConfigMap (values are never returned). Use it to check
        whether a key a container references exists."""

        def run() -> Any:
            cm = kube.get_configmap(namespace, name)
            return {"name": name,
                    "keys": sorted((cm.get("data") or {}).keys()),
                    "binary_keys": sorted((cm.get("binaryData") or {}).keys())}

        return _run("list_configmap_keys", run)

    @tool
    def get_argocd_application(name: str) -> str:
        """Read an Argo CD Application in the argocd namespace: sync and health status,
        conditions, the last operation's message, and its source."""

        def run() -> Any:
            app = kube.get_application("argocd", name)
            status = app.get("status") or {}
            operation = status.get("operationState") or {}
            return {
                "name": name,
                "source": (app.get("spec") or {}).get("source"),
                "sync": (status.get("sync") or {}).get("status"),
                "health": (status.get("health") or {}).get("status"),
                "conditions": [{k: c.get(k) for k in ("type", "message")}
                               for c in status.get("conditions") or []],
                "operation": {"phase": operation.get("phase"),
                              "message": bound_text(str(operation.get("message") or ""), 1_000)},
            }

        return _run("get_argocd_application", run)

    base = select(build_tools(kube), {"get_pod", "list_pods", "get_pod_logs", "list_events",
                                      "get_owner_workload"})
    return base + [get_rollout_history, get_workload_status, list_configmap_keys,
                   get_argocd_application]


# --- storage ------------------------------------------------------------------


def summarize_pvc(pvc: dict[str, Any]) -> dict[str, Any]:
    spec = pvc.get("spec") or {}
    status = pvc.get("status") or {}
    return {
        "name": _meta(pvc).get("name"),
        "storageClassName": spec.get("storageClassName"),
        "accessModes": spec.get("accessModes"),
        "requested": ((spec.get("resources") or {}).get("requests") or {}).get("storage"),
        "volumeName": spec.get("volumeName"),
        "phase": status.get("phase"),
        "capacity": (status.get("capacity") or {}).get("storage"),
        "conditions": [{k: c.get(k) for k in ("type", "status", "message")}
                       for c in status.get("conditions") or []],
    }


def storage_tools(kube: Any) -> list[Any]:
    @tool
    def get_pvc(namespace: str, name: str) -> str:
        """Read a PersistentVolumeClaim: StorageClass, requested size, access modes, bound
        volume, phase (Pending/Bound) and conditions."""
        return _run("get_pvc", lambda: summarize_pvc(kube.get_pvc(namespace, name)))

    @tool
    def list_pvcs(namespace: str) -> str:
        """List the PersistentVolumeClaims in a namespace with phase and StorageClass."""
        return _run("list_pvcs", lambda: [summarize_pvc(p) for p in
                                          _items(kube.list_pvcs(namespace))])

    @tool
    def get_pv(name: str) -> str:
        """Read a PersistentVolume: phase, claim, StorageClass, capacity and CSI source."""

        def run() -> Any:
            pv = kube.get_pv(name)
            spec = pv.get("spec") or {}
            return {"name": name, "phase": (pv.get("status") or {}).get("phase"),
                    "claim": spec.get("claimRef"), "storageClassName": spec.get("storageClassName"),
                    "capacity": (spec.get("capacity") or {}).get("storage"),
                    "csi": {k: (spec.get("csi") or {}).get(k) for k in ("driver", "volumeHandle")}}

        return _run("get_pv", run)

    @tool
    def list_storage_classes() -> str:
        """List the cluster's StorageClasses: provisioner, whether volumes can be expanded,
        binding mode, reclaim policy, and which one is the default."""

        def run() -> Any:
            return [
                {"name": _meta(sc).get("name"), "provisioner": sc.get("provisioner"),
                 "allowVolumeExpansion": bool(sc.get("allowVolumeExpansion")),
                 "volumeBindingMode": sc.get("volumeBindingMode"),
                 "reclaimPolicy": sc.get("reclaimPolicy"),
                 "default": (_meta(sc).get("annotations") or {})
                 .get("storageclass.kubernetes.io/is-default-class") == "true"}
                for sc in _items(kube.list_storage_classes())
            ]

        return _run("list_storage_classes", run)

    @tool
    def list_volume_attachments() -> str:
        """List VolumeAttachments: which volume is attached to which node, and any attach
        or detach error."""

        def run() -> Any:
            return [
                {"name": _meta(va).get("name"),
                 "node": (va.get("spec") or {}).get("nodeName"),
                 "pv": ((va.get("spec") or {}).get("source") or {}).get("persistentVolumeName"),
                 "attached": (va.get("status") or {}).get("attached"),
                 "attachError": ((va.get("status") or {}).get("attachError") or {}).get("message"),
                 "detachError": ((va.get("status") or {}).get("detachError") or {}).get("message")}
                for va in _items(kube.list_volume_attachments(), 100)
            ]

        return _run("list_volume_attachments", run)

    @tool
    def get_ebs_csi_status() -> str:
        """Read the EBS CSI controller: pod states and recent log lines from the ebs-plugin
        and csi-provisioner containers, where provisioning errors are logged."""

        def run() -> Any:
            pods = _items(kube.list_pods("kube-system", "app=ebs-csi-controller"), 5)
            name = _meta(pods[0]).get("name", "") if pods else ""
            return {
                "pods": [summarize_pod(p) for p in pods],
                "ebs_plugin_logs": _logs(kube, "kube-system", name, "ebs-plugin") if pods
                else "<no controller pods>",
                "provisioner_logs": _logs(kube, "kube-system", name, "csi-provisioner") if pods
                else "<no controller pods>",
            }

        return _run("get_ebs_csi_status", run)

    base = select(build_tools(kube), {"get_pod", "list_pods", "get_pod_logs", "list_events",
                                      "get_node"})
    return base + [get_pvc, list_pvcs, get_pv, list_storage_classes, list_volume_attachments,
                   get_ebs_csi_status]
