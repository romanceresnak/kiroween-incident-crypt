"""scenario-lab TASK-06: the lab namespaces, the runner and its permissions, as rendered by
helm (REQ-RN-01 to RN-04, RN-08, REQ-WT-02). Normal tests."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).parents[2]
CHART = REPO / "gitops" / "charts" / "agents"
LAB_SRC = REPO / "agents" / "orchestrator" / "src" / "kiroween_orchestrator" / "lab"
SHA = "0123456789abcdef0123456789abcdef01234567"
LAB_NAMESPACES = [f"lab-{n:02d}" for n in range(1, 15)]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def render(*sets: str, tag: str = SHA) -> list[dict]:
    args = ["helm", "template", "agents", str(CHART), "--namespace", "agents",
            "--set", f"images.orchestrator.tag={tag}"]
    for item in sets:
        args += ["--set", item]
    out = subprocess.run(args, check=True, capture_output=True, text=True).stdout  # noqa: S603
    return [doc for doc in yaml.safe_load_all(out) if doc]


@pytest.fixture(scope="module")
def docs() -> list[dict]:
    return render()


def kind(docs: list[dict], name: str) -> list[dict]:
    return [d for d in docs if d["kind"] == name]


def named(docs: list[dict], k: str, name: str) -> dict:
    (found,) = [d for d in kind(docs, k) if d["metadata"]["name"] == name]
    return found


def in_namespace(docs: list[dict], k: str, namespace: str) -> list[dict]:
    return [d for d in kind(docs, k) if d["metadata"].get("namespace") == namespace]


# ------------------------------------------------------------- the 14 namespaces (RN-08)


def test_fourteen_lab_namespaces_exist_with_the_lab_and_pod_security_labels(docs) -> None:
    spaces = {d["metadata"]["name"]: d["metadata"]["labels"] for d in kind(docs, "Namespace")}
    assert sorted(spaces) == LAB_NAMESPACES
    for name, labels in spaces.items():
        assert labels["kiroween.io/lab"] == "true", name
        assert labels["pod-security.kubernetes.io/enforce"] == "baseline", name


def test_each_namespace_has_one_quota_and_one_limit_range(docs) -> None:
    for ns in LAB_NAMESPACES:
        (quota,) = in_namespace(docs, "ResourceQuota", ns)
        (limits,) = in_namespace(docs, "LimitRange", ns)
        assert quota["spec"]["hard"] == {
            "requests.cpu": "500m", "requests.memory": "512Mi", "limits.cpu": "1",
            "limits.memory": "1Gi", "pods": "10", "persistentvolumeclaims": "2",
            "requests.storage": "2Gi", "services": "4", "configmaps": "6"}, ns
        (limit,) = limits["spec"]["limits"]
        assert limit["type"] == "Container"
        assert limit["max"] == {"cpu": "200m", "memory": "128Mi"}


def test_the_limit_range_maximum_is_what_the_scenario_renderer_allows() -> None:
    render_py = (LAB_SRC / "render.py").read_text()
    found = re.search(r"MAX_CPU_M, MAX_MEMORY_MI, MAX_OBJECTS = (\d+), (\d+), (\d+)", render_py)
    assert found
    values = yaml.safe_load((CHART / "values.yaml").read_text())["lab"]["limitRange"]["max"]
    assert values == {"cpu": f"{found.group(1)}m", "memory": f"{found.group(2)}Mi"}


def test_the_namespaces_match_the_ones_the_scenarios_and_the_runner_use() -> None:
    scenarios = (LAB_SRC / "scenarios.py").read_text()
    assert sorted(set(re.findall(r'"(lab-\d\d)"', scenarios))) == LAB_NAMESPACES
    kube = (LAB_SRC / "kube.py").read_text()
    assert 'LAB_NAMESPACE = re.compile(r"lab-(0[1-9]|1[0-4])")' in kube


def test_the_watcher_does_not_exclude_a_lab_namespace(docs) -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    assert not set(LAB_NAMESPACES) & set(values["watcher"]["excludedNamespaces"])
    watcher = named(docs, "Deployment", "orchestrator-watcher")
    env = {e["name"]: e.get("value") for e in
           watcher["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert not set(LAB_NAMESPACES) & set(env["EXCLUDED_NAMESPACES"].split(","))


# ------------------------------------------------------- the runner's permissions (RN-02)

# What each Kubernetes client call needs, as (API group, resource, verb).
CALLS: dict[str, tuple[str, str, str]] = {
    "create_namespaced_deployment": ("apps", "deployments", "create"),
    "patch_namespaced_deployment": ("apps", "deployments", "patch"),
    "read_namespaced_deployment": ("apps", "deployments", "get"),
    "list_namespaced_deployment": ("apps", "deployments", "list"),
    "delete_collection_namespaced_deployment": ("apps", "deployments", "deletecollection"),
    "create_namespaced_service": ("", "services", "create"),
    "list_namespaced_service": ("", "services", "list"),
    "delete_namespaced_service": ("", "services", "delete"),
    "list_namespaced_pod": ("", "pods", "list"),
    "delete_collection_namespaced_pod": ("", "pods", "deletecollection"),
    "create_namespaced_config_map": ("", "configmaps", "create"),
    "list_namespaced_config_map": ("", "configmaps", "list"),
    "delete_collection_namespaced_config_map": ("", "configmaps", "deletecollection"),
    "create_namespaced_persistent_volume_claim": ("", "persistentvolumeclaims", "create"),
    "list_namespaced_persistent_volume_claim": ("", "persistentvolumeclaims", "list"),
    "delete_collection_namespaced_persistent_volume_claim": (
        "", "persistentvolumeclaims", "deletecollection"),
    "create_namespaced_network_policy": ("networking.k8s.io", "networkpolicies", "create"),
    "list_namespaced_network_policy": ("networking.k8s.io", "networkpolicies", "list"),
    "delete_collection_namespaced_network_policy": (
        "networking.k8s.io", "networkpolicies", "deletecollection"),
}


def calls_in_kube_py() -> set[str]:
    tree = ast.parse((LAB_SRC / "kube.py").read_text())
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
            and re.match(r"(create|patch|read|list|delete)_", n.attr)}


def granted(role: dict) -> set[tuple[str, str, str]]:
    return {(group, resource, verb) for rule in role["rules"]
            for group in rule["apiGroups"] for resource in rule["resources"]
            for verb in rule["verbs"]}


def test_the_runner_role_grants_exactly_what_kube_py_calls(docs) -> None:
    calls = calls_in_kube_py() - {"read_namespace"}
    unknown = calls - set(CALLS)
    assert not unknown, f"kube.py makes a call this test does not know: {unknown}"
    needed = {CALLS[c] for c in calls}
    assert granted(named(docs, "ClusterRole", "kiroween-lab-runner")) == needed


def test_the_runner_role_has_nothing_dangerous(docs) -> None:
    role = named(docs, "ClusterRole", "kiroween-lab-runner")
    allowed_verbs = {"create", "get", "list", "patch", "delete", "deletecollection"}
    allowed_resources = {"deployments", "pods", "services", "configmaps",
                         "persistentvolumeclaims", "networkpolicies"}
    for rule in role["rules"]:
        assert set(rule["verbs"]) <= allowed_verbs, rule
        assert set(rule["resources"]) <= allowed_resources, rule
        for key in ("verbs", "resources", "apiGroups"):
            assert "*" not in rule[key], rule
        assert "resourceNames" not in rule
    text = yaml.safe_dump(role)
    for word in ("secrets", "roles", "rolebindings", "clusterroles", "nodes", "exec", "attach",
                 "portforward", "proxy", "escalate", "bind", "impersonate", "serviceaccounts",
                 "tokenreviews", "customresourcedefinitions", "update", "watch"):
        assert word not in text, word
    # Pods cannot be created, only found and deleted: a run's pods come from its Deployment.
    pods = [r for r in role["rules"] if "pods" in r["resources"]]
    assert pods == [{"apiGroups": [""], "resources": ["pods"],
                     "verbs": ["list", "deletecollection"]}]


def test_the_runner_can_read_only_the_fourteen_lab_namespaces_by_name(docs) -> None:
    role = named(docs, "ClusterRole", "kiroween-lab-runner-namespaces")
    assert role["rules"] == [{"apiGroups": [""], "resources": ["namespaces"],
                              "resourceNames": LAB_NAMESPACES, "verbs": ["get"]}]
    assert "read_namespace" in calls_in_kube_py()
    binding = named(docs, "ClusterRoleBinding", "kiroween-lab-runner-namespaces")
    assert binding["roleRef"]["name"] == "kiroween-lab-runner-namespaces"
    assert binding["subjects"] == [{"kind": "ServiceAccount", "name": "lab-runner-sa",
                                    "namespace": "agents"}]


def test_the_write_role_is_bound_only_inside_the_lab_namespaces(docs) -> None:
    bindings = [d for d in kind(docs, "RoleBinding")
                if d["roleRef"]["name"] == "kiroween-lab-runner"]
    assert sorted(b["metadata"]["namespace"] for b in bindings) == LAB_NAMESPACES
    for b in bindings:
        assert b["roleRef"] == {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole",
                                "name": "kiroween-lab-runner"}
        assert b["subjects"] == [{"kind": "ServiceAccount", "name": "lab-runner-sa",
                                  "namespace": "agents"}]
    # Never bound for the whole cluster, and never to anyone else.
    for cluster_binding in kind(docs, "ClusterRoleBinding"):
        assert cluster_binding["roleRef"]["name"] != "kiroween-lab-runner"
    for any_binding in kind(docs, "RoleBinding") + kind(docs, "ClusterRoleBinding"):
        if any_binding["roleRef"]["name"].startswith("kiroween-lab-runner"):
            assert [s["name"] for s in any_binding["subjects"]] == ["lab-runner-sa"]


def test_no_agent_gains_a_write_permission(docs) -> None:
    """REQ-RN-03: only lab-runner-sa is bound to anything that can write."""
    writers = {"create", "patch", "update", "delete", "deletecollection"}
    roles = kind(docs, "ClusterRole") + kind(docs, "Role")
    writing_roles = {d["metadata"]["name"] for d in roles
                     if any(set(r["verbs"]) & writers for r in d["rules"])}
    assert writing_roles == {"kiroween-lab-runner"}
    bindings = kind(docs, "RoleBinding") + kind(docs, "ClusterRoleBinding")
    for binding in bindings:
        if binding["roleRef"]["name"] in writing_roles:
            assert {s["name"] for s in binding["subjects"]} == {"lab-runner-sa"}
    for name in ("orchestrator-sa", "orchestrator-watcher-sa", "cpu-memory-sa", "network-sa",
                 "deploy-rollout-sa", "storage-sa", "incident-memory-sa"):
        for binding in bindings:
            if name in [s["name"] for s in binding["subjects"]]:
                assert binding["roleRef"]["name"] not in writing_roles, (name, binding)


# --------------------------------------------------------------------- the runner (RN-01)


def runner(docs) -> dict:
    return named(docs, "Deployment", "lab-runner")


def test_the_runner_is_the_orchestrator_image_with_the_lab_entrypoint(docs) -> None:
    pod = runner(docs)["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    orchestrator = named(docs, "Deployment", "orchestrator")["spec"]["template"]["spec"][
        "containers"][0]
    assert container["image"] == orchestrator["image"] and SHA in container["image"]
    assert container["args"] == ["-m", "kiroween_orchestrator.lab"]
    assert pod["serviceAccountName"] == "lab-runner-sa"
    assert runner(docs)["spec"]["strategy"] == {"type": "Recreate"}
    assert runner(docs)["spec"]["replicas"] == 1


def test_the_runner_is_hardened_like_every_other_pod(docs) -> None:
    pod = runner(docs)["spec"]["template"]["spec"]
    psc = pod["securityContext"]
    assert psc["runAsNonRoot"] is True and psc["runAsUser"] == 10001
    assert psc["seccompProfile"] == {"type": "RuntimeDefault"}
    csc = pod["containers"][0]["securityContext"]
    assert csc == {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                   "capabilities": {"drop": ["ALL"]}}
    assert set(pod["containers"][0]["resources"]) == {"requests", "limits"}
    assert pod["containers"][0]["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert pod["containers"][0]["livenessProbe"]["httpGet"]["path"] == "/healthz"


def test_the_runner_has_no_aws_identity_no_secret_and_only_its_settings(docs) -> None:
    pod = runner(docs)["spec"]["template"]["spec"]
    env = {e["name"]: e for e in pod["containers"][0]["env"]}
    assert set(env) == {"HOME", "LOG_LEVEL", "API_PORT", "LAB_MAX_RUNS_PER_HOUR",
                        "LAB_CLEANUP_SECONDS"}
    assert env["LAB_MAX_RUNS_PER_HOUR"]["value"] == "10"
    assert env["LAB_CLEANUP_SECONDS"]["value"] == "900"
    assert "valueFrom" not in yaml.safe_dump(env) and "secret" not in yaml.safe_dump(pod).lower()
    account = named(docs, "ServiceAccount", "lab-runner-sa")
    assert "annotations" not in account["metadata"]
    infra = (REPO / "infra" / "variables.tf").read_text()
    agents = re.search(r'variable "agent_names".*?default\s*=\s*\[([^\]]*)\]', infra, re.S)
    assert agents and "lab-runner" not in agents.group(1), "no Pod Identity role for the runner"


def test_the_runner_service_is_cluster_internal(docs) -> None:
    service = named(docs, "Service", "lab-runner")
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["ports"] == [{"name": "api", "port": 8080, "targetPort": "api"}]
    assert service["spec"]["selector"] == {"app.kubernetes.io/name": "lab-runner"}
    assert not [d for d in docs if d["kind"] in ("Ingress", "TargetGroupBinding")
                and "lab-runner" in yaml.safe_dump(d)]


def test_only_the_orchestrator_can_reach_the_runner(docs) -> None:
    orchestrator_peer = {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "orchestrator"}}}
    policy = named(docs, "NetworkPolicy", "allow-orchestrator-to-lab-runner")
    assert policy["spec"]["podSelector"] == {
        "matchLabels": {"app.kubernetes.io/name": "lab-runner"}}
    assert policy["spec"]["policyTypes"] == ["Ingress"]
    assert policy["spec"]["ingress"] == [{"from": [orchestrator_peer],
                                          "ports": [{"protocol": "TCP", "port": 8080}]}]
    egress = named(docs, "NetworkPolicy", "allow-orchestrator-egress-lab-runner")
    assert egress["spec"]["egress"] == [{
        "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "lab-runner"}}}],
        "ports": [{"protocol": "TCP", "port": 8080}]}]
    for other in kind(docs, "NetworkPolicy"):
        sel = other["spec"].get("podSelector", {}).get("matchLabels", {})
        if sel.get("app.kubernetes.io/name") == "lab-runner":
            assert other["metadata"]["name"] == "allow-orchestrator-to-lab-runner"


def test_the_orchestrator_is_told_where_the_runner_is(docs) -> None:
    env = {e["name"]: e.get("value") for e in
           named(docs, "Deployment", "orchestrator")["spec"]["template"]["spec"][
               "containers"][0]["env"]}
    assert env["LAB_RUNNER_URL"] == "http://lab-runner:8080"
    assert named(docs, "Service", "lab-runner")["spec"]["ports"][0]["port"] == 8080


# -------------------------------------------------------------------------- switches


def test_the_lab_can_be_switched_off_completely() -> None:
    docs = render("lab.enabled=false")
    assert not kind(docs, "Namespace") and not kind(docs, "ResourceQuota")
    assert "lab-runner" not in {d["metadata"]["name"] for d in docs}
    assert not [d for d in kind(docs, "ClusterRole") if "lab" in d["metadata"]["name"]]
    env = {e["name"] for e in named(docs, "Deployment", "orchestrator")["spec"]["template"][
        "spec"]["containers"][0]["env"]}
    assert "LAB_RUNNER_URL" not in env


def test_without_an_image_tag_the_namespaces_exist_but_no_runner_runs() -> None:
    docs = render(tag="unset")
    assert len(kind(docs, "Namespace")) == 14
    assert not [d for d in kind(docs, "Deployment") if d["metadata"]["name"] == "lab-runner"]


def test_a_latest_tag_is_refused_for_the_runner_too() -> None:
    with pytest.raises(subprocess.CalledProcessError) as info:
        render(tag="latest")
    assert "not latest" in info.value.stderr


def test_the_namespaces_are_left_alone_when_network_policies_are_off() -> None:
    docs = render("networkPolicy.enabled=false")
    assert len(kind(docs, "Namespace")) == 14
    assert not [d for d in kind(docs, "NetworkPolicy") if "lab-runner" in d["metadata"]["name"]]


def test_the_limits_are_values() -> None:
    docs = render("lab.maxRunsPerHour=3", "lab.cleanupSeconds=60")
    env = {e["name"]: e["value"] for e in runner(docs)["spec"]["template"]["spec"][
        "containers"][0]["env"]}
    assert (env["LAB_MAX_RUNS_PER_HOUR"], env["LAB_CLEANUP_SECONDS"]) == ("3", "60")
