"""specialists TASK-02: read-only client methods and the three agents' tools."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from kubernetes import client as k8s

from kiroween_shared.k8s.client import KubeError
from kiroween_shared.k8s.extra_tools import deploy_tools, network_tools, storage_tools
from kiroween_shared.testing import LEAK_CANARY, FakeCluster, obj

CLIENT = Path(__file__).parents[1] / "src" / "kiroween_shared" / "k8s" / "client.py"
API_CLASSES = {
    "_core": k8s.CoreV1Api, "_apps": k8s.AppsV1Api, "_autoscaling": k8s.AutoscalingV2Api,
    "_custom": k8s.CustomObjectsApi, "_discovery": k8s.DiscoveryV1Api,
    "_networking": k8s.NetworkingV1Api, "_storage": k8s.StorageV1Api,
}


def payload(result: str) -> object:
    return json.loads(result.split("\n", 1)[1].rsplit("\n", 1)[0])


def by_name(tools: list) -> dict:
    return {t.tool_name: t for t in tools}


def test_every_client_method_called_exists_in_the_kubernetes_client() -> None:
    """Fakes cannot catch a typo in a real API method name; this can."""
    called = set()
    for node in ast.walk(ast.parse(CLIENT.read_text())):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name) and node.value.value.id == "self"
                and node.value.attr in API_CLASSES):
            called.add((node.value.attr, node.attr))
    assert len(called) > 25
    missing = [f"{api}.{m}" for api, m in called if not hasattr(API_CLASSES[api], m)]
    assert missing == []


def test_new_methods_use_read_verbs_only() -> None:
    api_attrs = "|".join(a.lstrip("_") for a in API_CLASSES)
    names = set(re.findall(rf"self\._(?:{api_attrs})\.(\w+)", CLIENT.read_text()))
    assert names and all(n.startswith(("read_", "list_", "get_")) for n in names), names


def test_tool_sets_per_agent() -> None:
    cluster = FakeCluster()
    assert set(by_name(network_tools(cluster))) == {
        "get_pod", "list_pods", "get_pod_logs", "list_events", "get_service", "list_services",
        "get_endpoints", "list_network_policies", "list_ingresses", "get_coredns_status"}
    assert set(by_name(deploy_tools(cluster))) == {
        "get_pod", "list_pods", "get_pod_logs", "list_events", "get_owner_workload",
        "get_rollout_history", "get_workload_status", "list_configmap_keys",
        "get_argocd_application"}
    assert set(by_name(storage_tools(cluster))) == {
        "get_pod", "list_pods", "get_pod_logs", "list_events", "get_node", "get_pvc",
        "list_pvcs", "get_pv", "list_storage_classes", "list_volume_attachments",
        "get_ebs_csi_status"}


# --- network ------------------------------------------------------------------


def test_endpoints_count_ready_and_not_ready() -> None:
    cluster = FakeCluster([obj("EndpointSlice", "web-x", "shop",
                               metadata={"labels": {"kubernetes.io/service-name": "web"}},
                               endpoints=[{"addresses": ["10.0.1.5"], "conditions": {"ready": True},
                                           "targetRef": {"name": "web-1"}},
                                          {"addresses": ["10.0.1.6"],
                                           "conditions": {"ready": False}}])])
    data = payload(by_name(network_tools(cluster))["get_endpoints"](namespace="shop",
                                                                     service="web"))
    assert (data["ready"], data["not_ready"], data["slices"]) == (1, 1, 1)


def test_coredns_status_returns_pods_logs_and_corefile() -> None:
    cluster = FakeCluster(
        [obj("Pod", "coredns-1", "kube-system", metadata={"labels": {"k8s-app": "kube-dns"}}),
         obj("ConfigMap", "coredns", "kube-system",
             data={"Corefile": ".:53 { forward . /etc/resolv.conf }"})],
        logs={("kube-system", "coredns-1", "coredns"): "[ERROR] plugin/errors: i/o timeout"})
    data = payload(by_name(network_tools(cluster))["get_coredns_status"]())
    assert data["corefile"].startswith(".:53")
    assert "i/o timeout" in data["recent_logs"]
    assert data["pods"][0]["name"] == "coredns-1"


def test_network_policy_summary() -> None:
    policy = obj("NetworkPolicy", "allow-trusted", "shop",
                 spec={"podSelector": {"matchLabels": {"app": "server"}},
                       "policyTypes": ["Ingress"],
                       "ingress": [{"from": [
                           {"podSelector": {"matchLabels": {"app": "trusted"}}}]}]})
    data = payload(by_name(network_tools(FakeCluster([policy])))["list_network_policies"](
        namespace="shop"))
    assert data[0]["ingress"][0]["from"][0]["podSelector"]["matchLabels"] == {"app": "trusted"}


# --- deploy-rollout -------------------------------------------------------------


def deployment_with_history() -> FakeCluster:
    labels = {"app": "web"}
    rs = lambda name, rev, image, ready, owner="web": obj(  # noqa: E731
        "ReplicaSet", name, "shop",
        metadata={"labels": labels, "annotations": {"deployment.kubernetes.io/revision": rev},
                  "ownerReferences": [{"kind": "Deployment", "name": owner}]},
        spec={"replicas": 1,
              "template": {"spec": {"containers": [{"name": "web", "image": image}]}}},
        status={"readyReplicas": ready})
    return FakeCluster([
        obj("Deployment", "web", "shop", spec={"selector": {"matchLabels": labels}}),
        rs("web-a", "1", "repo/web:1.0", 1),
        rs("web-b", "2", "repo/web:1.1-bad", 0),
        rs("other-c", "5", "repo/other:9", 1, owner="other"),
    ])


def test_rollout_history_newest_first_and_owned_only() -> None:
    data = payload(by_name(deploy_tools(deployment_with_history()))["get_rollout_history"](
        namespace="shop", deployment="web"))
    assert [(h["revision"], h["images"]["web"], h["ready"]) for h in data] == [
        (2, "repo/web:1.1-bad", 0), (1, "repo/web:1.0", 1)]


def test_configmap_tool_returns_key_names_only() -> None:
    cluster = FakeCluster([obj("ConfigMap", "app-config", "shop",
                               data={"DATABASE_URL": LEAK_CANARY, "MODE": "prod"})])
    result = by_name(deploy_tools(cluster))["list_configmap_keys"](namespace="shop",
                                                                   name="app-config")
    assert LEAK_CANARY not in result and "prod" not in result
    assert payload(result)["keys"] == ["DATABASE_URL", "MODE"]


def test_argocd_application_status() -> None:
    app = obj("Application", "agents", "argocd",
              spec={"source": {"path": "gitops/charts/agents"}},
              status={"sync": {"status": "OutOfSync"}, "health": {"status": "Degraded"},
                      "conditions": [{"type": "ComparisonError", "message": "boom"}]})
    data = payload(by_name(deploy_tools(FakeCluster([app])))["get_argocd_application"](
        name="agents"))
    assert (data["sync"], data["health"]) == ("OutOfSync", "Degraded")


# --- storage ------------------------------------------------------------------


def test_pvc_and_storage_classes() -> None:
    cluster = FakeCluster([
        obj("PersistentVolumeClaim", "data", "shop",
            spec={"storageClassName": "missing", "resources": {"requests": {"storage": "1Gi"}}},
            status={"phase": "Pending"}),
        obj("StorageClass", "gp2", provisioner="kubernetes.io/aws-ebs",
            metadata={"annotations": {"storageclass.kubernetes.io/is-default-class": "true"}}),
    ])
    tools = by_name(storage_tools(cluster))
    pvc = payload(tools["get_pvc"](namespace="shop", name="data"))
    assert (pvc["storageClassName"], pvc["phase"], pvc["requested"]) == ("missing", "Pending",
                                                                        "1Gi")
    classes = payload(tools["list_storage_classes"]())
    assert classes == [{"name": "gp2", "provisioner": "kubernetes.io/aws-ebs",
                        "allowVolumeExpansion": False, "volumeBindingMode": None,
                        "reclaimPolicy": None, "default": True}]


def test_ebs_csi_status_reads_plugin_and_provisioner_logs() -> None:
    cluster = FakeCluster(
        [obj("Pod", "ebs-csi-controller-1", "kube-system",
             metadata={"labels": {"app": "ebs-csi-controller"}})],
        logs={("kube-system", "ebs-csi-controller-1", "ebs-plugin"): "plugin ok",
              ("kube-system", "ebs-csi-controller-1", "csi-provisioner"):
                  "failed to provision volume: UnauthorizedOperation"})
    data = payload(by_name(storage_tools(cluster))["get_ebs_csi_status"]())
    assert "UnauthorizedOperation" in data["provisioner_logs"]
    assert data["ebs_plugin_logs"] == "plugin ok"


# --- errors and bounds -----------------------------------------------------------


@pytest.mark.parametrize("tool_set,name,kwargs", [
    (network_tools, "get_service", {"namespace": "shop", "name": "gone"}),
    (deploy_tools, "get_rollout_history", {"namespace": "shop", "deployment": "gone"}),
    (deploy_tools, "list_configmap_keys", {"namespace": "shop", "name": "gone"}),
    (storage_tools, "get_pvc", {"namespace": "shop", "name": "gone"}),
    (storage_tools, "get_pv", {"name": "gone"}),
])
def test_missing_objects_are_results_not_exceptions(tool_set, name: str, kwargs: dict) -> None:
    assert payload(by_name(tool_set(FakeCluster()))[name](**kwargs))["error"] == "not_found"


def test_forbidden_is_a_result() -> None:
    cluster = FakeCluster(errors={"get_service": KubeError(403, "forbidden", "rbac")})
    data = payload(by_name(network_tools(cluster))["get_service"](namespace="shop", name="web"))
    assert (data["error"], data["status"]) == ("forbidden", 403)


def test_coredns_logs_are_bounded() -> None:
    cluster = FakeCluster(
        [obj("Pod", "coredns-1", "kube-system", metadata={"labels": {"k8s-app": "kube-dns"}})],
        logs={("kube-system", "coredns-1", "coredns"): "x" * 200_000})
    result = by_name(network_tools(cluster))["get_coredns_status"]()
    assert len(result) < 12_000
