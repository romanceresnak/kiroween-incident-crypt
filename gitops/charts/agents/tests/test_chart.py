"""TASK-10 chart tests: render with helm and check the security properties.

Run with any Python that has pytest and PyYAML, and helm on PATH:
    python -m pytest gitops/charts/agents/tests
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).parents[1]
REPO = CHART.parents[2]
NAMESPACE = "agents"
SHA = "0123456789abcdef0123456789abcdef01234567"
READ_VERBS = {"get", "list", "watch"}
IMAGE_KEYS = ["orchestrator", "cpuMemory", "network", "deployRollout", "storage"]
SPECIALISTS = ["cpu-memory", "network", "deploy-rollout", "storage"]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def render(*sets: str) -> list[dict]:
    args = ["helm", "template", "agents", str(CHART), "--namespace", NAMESPACE]
    for item in sets:
        args += ["--set", item]
    out = subprocess.run(args, check=True, capture_output=True, text=True).stdout  # noqa: S603
    return [doc for doc in yaml.safe_load_all(out) if doc]


@pytest.fixture(scope="module")
def docs() -> list[dict]:
    return render(*(f"images.{key}.tag={SHA}" for key in IMAGE_KEYS))


def of_kind(docs: list[dict], kind: str) -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in docs if d["kind"] == kind}


# --- RBAC (REQ-CHT-04, CHT-05) ---------------------------------------------


def test_rbac_is_read_only_and_never_touches_secrets(docs: list[dict]) -> None:
    roles = [d for d in docs if d["kind"] in ("Role", "ClusterRole")]
    assert roles
    for role in roles:
        for rule in role.get("rules", []):
            assert set(rule["verbs"]) <= READ_VERBS, (role["metadata"]["name"], rule)
            for key in ("verbs", "resources", "apiGroups"):
                assert "*" not in rule.get(key, []), (role["metadata"]["name"], key)
            assert "secrets" not in rule.get("resources", [])


def test_role_bindings(docs: list[dict]) -> None:
    bindings = of_kind(docs, "ClusterRoleBinding")
    subjects = {
        name: [(s["name"], s["namespace"]) for s in b["subjects"]] for name, b in bindings.items()
    }
    assert subjects == {
        "kiroween-agents-watcher": [("orchestrator-watcher-sa", NAMESPACE)],
        **{f"kiroween-agents-{name}": [(f"{name}-sa", NAMESPACE)] for name in SPECIALISTS},
    }


def test_watcher_can_only_read_pods_and_events(docs: list[dict]) -> None:
    rules = of_kind(docs, "ClusterRole")["kiroween-agents-watcher"]["rules"]
    assert rules == [{"apiGroups": [""], "resources": ["pods", "events"],
                      "verbs": ["get", "list", "watch"]}]


def test_orchestrator_has_no_kubernetes_permissions(docs: list[dict]) -> None:
    for binding in of_kind(docs, "ClusterRoleBinding").values():
        assert all(s["name"] != "orchestrator-sa" for s in binding["subjects"])


# --- ServiceAccounts match infra Pod Identity (REQ-CHT-02) ------------------


def test_service_accounts_match_infra_pod_identity(docs: list[dict]) -> None:
    infra = (REPO / "infra" / "modules" / "pod_identity" / "main.tf").read_text()
    variables = (REPO / "infra" / "modules" / "pod_identity" / "variables.tf").read_text()
    assert '"${a}-sa"' in infra, "infra service account naming changed"
    assert re.search(r'variable "namespace".*?default\s*=\s*"agents"', variables, re.S)
    names = set(of_kind(docs, "ServiceAccount"))
    assert {"orchestrator-sa", *(f"{name}-sa" for name in SPECIALISTS)} <= names
    assert "orchestrator-watcher-sa" in names


# --- Deployments (REQ-CHT-01, CHT-03, CHT-06, CHT-07) ----------------------


def test_unset_tags_render_no_deployments() -> None:
    # values.yaml carries CI-written tags, so set every tag back to "unset" explicitly.
    docs = render(*(f"images.{key}.tag=unset" for key in IMAGE_KEYS))
    assert of_kind(docs, "Deployment") == {}


def test_latest_tag_is_refused() -> None:
    with pytest.raises(subprocess.CalledProcessError) as info:
        render("images.orchestrator.tag=latest")
    assert "not latest" in info.value.stderr


def test_six_deployments_with_expected_service_accounts(docs: list[dict]) -> None:
    deployments = of_kind(docs, "Deployment")
    assert {n: d["spec"]["template"]["spec"]["serviceAccountName"]
            for n, d in deployments.items()} == {
        "orchestrator": "orchestrator-sa",
        "orchestrator-watcher": "orchestrator-watcher-sa",
        **{name: f"{name}-sa" for name in SPECIALISTS},
    }


@pytest.mark.parametrize("name", ["orchestrator", "orchestrator-watcher", *SPECIALISTS])
def test_pod_hardening(docs: list[dict], name: str) -> None:
    pod = of_kind(docs, "Deployment")[name]["spec"]["template"]["spec"]
    psc = pod["securityContext"]
    assert psc["runAsNonRoot"] is True and psc["runAsUser"] == 10001
    assert psc["seccompProfile"]["type"] == "RuntimeDefault"
    (container,) = pod["containers"]
    csc = container["securityContext"]
    assert csc["readOnlyRootFilesystem"] is True
    assert csc["allowPrivilegeEscalation"] is False
    assert csc["capabilities"]["drop"] == ["ALL"]
    assert container["resources"]["limits"] and container["resources"]["requests"]
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["image"].endswith(f":{SHA}")
    env_names = {e["name"] for e in container.get("env", [])}
    assert not env_names & {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}


def test_models_and_watcher_entrypoint(docs: list[dict]) -> None:
    deployments = of_kind(docs, "Deployment")

    def env(name: str) -> dict[str, str]:
        container = deployments[name]["spec"]["template"]["spec"]["containers"][0]
        return {e["name"]: e.get("value") for e in container["env"]}

    assert env("orchestrator")["MODEL_ID"].startswith("eu.")
    assert env("cpu-memory")["MODEL_ID"] == "eu.anthropic.claude-sonnet-4-6"
    assert "MODEL_ID" not in env("orchestrator-watcher")
    watcher = deployments["orchestrator-watcher"]["spec"]["template"]["spec"]["containers"][0]
    assert watcher["args"] == ["-m", "kiroween_orchestrator.watcher"]
    assert "chaos-oom" not in env("orchestrator-watcher")["EXCLUDED_NAMESPACES"]


def test_every_configured_model_has_a_price_the_app_can_load(docs: list[dict]) -> None:
    from kiroween_shared.metrics import PriceTable

    raw = of_kind(docs, "ConfigMap")["agents-config"]["data"]["prices.json"]
    table = PriceTable.from_json(raw)  # the same loader the agents use at startup
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    for model_id in values["models"].values():
        assert table.cost(model_id, 1_000_000, 1_000_000) is not None, model_id
    haiku = values["models"]["orchestrator"]
    assert table.cost(haiku, 1_000_000, 0) == pytest.approx(1.10)
    assert table.cost(haiku, 0, 1_000_000) == pytest.approx(5.50)


# --- Network policies (REQ-NET) ---------------------------------------------


def peers(policy: dict, direction: str) -> list[dict]:
    key = "from" if direction == "ingress" else "to"
    return [p for rule in policy["spec"].get(direction, []) for p in rule.get(key, [])]


def test_default_deny_covers_all_pods_both_directions(docs: list[dict]) -> None:
    deny = of_kind(docs, "NetworkPolicy")["default-deny"]
    assert deny["spec"]["podSelector"] == {}
    assert set(deny["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    assert "ingress" not in deny["spec"] and "egress" not in deny["spec"]


def test_policies_stay_in_the_agents_namespace(docs: list[dict]) -> None:
    for policy in of_kind(docs, "NetworkPolicy").values():
        assert policy["metadata"].get("namespace", NAMESPACE) == NAMESPACE


def test_ingress_sources_are_only_the_allowed_ones(docs: list[dict]) -> None:
    allowed = {
        "allow-watcher-to-orchestrator": [{"podSelector": {"matchLabels": {
            "app.kubernetes.io/name": "orchestrator-watcher"}}}],
        **{f"allow-orchestrator-to-{name}": [{"podSelector": {"matchLabels": {
            "app.kubernetes.io/name": "orchestrator"}}}] for name in SPECIALISTS},
        "allow-metrics-scrape": [{"namespaceSelector": {"matchLabels": {
            "kubernetes.io/metadata.name": "monitoring"}}}],
    }
    actual = {name: peers(p, "ingress") for name, p in of_kind(docs, "NetworkPolicy").items()
              if "Ingress" in p["spec"]["policyTypes"] and name != "default-deny"}
    assert actual == allowed


def test_metrics_scrape_reaches_only_the_metrics_port(docs: list[dict]) -> None:
    rule = of_kind(docs, "NetworkPolicy")["allow-metrics-scrape"]["spec"]["ingress"][0]
    assert rule["ports"] == [{"protocol": "TCP", "port": 9090}]


def test_pod_to_pod_egress_is_only_watcher_and_orchestrator(docs: list[dict]) -> None:
    pod_egress: dict[str, list[str]] = {}
    for name, policy in of_kind(docs, "NetworkPolicy").items():
        for peer in peers(policy, "egress"):
            if "podSelector" in peer and "namespaceSelector" not in peer:
                pod_egress.setdefault(name, []).append(
                    peer["podSelector"]["matchLabels"]["app.kubernetes.io/name"])
    assert pod_egress == {"allow-watcher-egress": ["orchestrator"],
                          "allow-orchestrator-egress": SPECIALISTS}


def test_dns_https_and_pod_identity_egress(docs: list[dict]) -> None:
    policies = of_kind(docs, "NetworkPolicy")
    dns = policies["allow-dns"]["spec"]["egress"][0]
    assert {p["port"] for p in dns["ports"]} == {53}
    assert {p["protocol"] for p in dns["ports"]} == {"UDP", "TCP"}
    https = policies["allow-https"]["spec"]["egress"][0]
    assert https["ports"] == [{"protocol": "TCP", "port": 443}]
    agent = policies["allow-pod-identity-agent"]["spec"]["egress"][0]
    assert agent["to"] == [{"ipBlock": {"cidr": "169.254.170.23/32"}}]
    assert agent["ports"] == [{"protocol": "TCP", "port": 80}]


def test_no_ingress_from_ip_blocks(docs: list[dict]) -> None:
    for policy in of_kind(docs, "NetworkPolicy").values():
        assert all("ipBlock" not in p for p in peers(policy, "ingress"))


def test_network_policies_can_be_disabled() -> None:
    assert of_kind(render("networkPolicy.enabled=false"), "NetworkPolicy") == {}


def test_service_monitor_off_by_default_and_renders_when_enabled() -> None:
    assert of_kind(render(), "ServiceMonitor") == {}
    assert set(of_kind(render("metrics.serviceMonitor.enabled=true"), "ServiceMonitor")) == {
        "orchestrator", "cpu-memory"}


# --- specialists spec (REQ-CHX, REQ-TLS-03, REQ-TLS-04) -------------------------------

SPECIALIST_RULES = {
    "kiroween-agents-network": {
        "": {"pods", "pods/log", "events", "services"},
        "discovery.k8s.io": {"endpointslices"},
        "networking.k8s.io": {"networkpolicies", "ingresses"},
    },
    "kiroween-agents-deploy-rollout": {
        "": {"pods", "pods/log", "events", "configmaps"},
        "apps": {"deployments", "replicasets", "statefulsets", "daemonsets"},
    },
    "kiroween-agents-storage": {
        "": {"pods", "pods/log", "events", "persistentvolumeclaims", "persistentvolumes",
             "nodes"},
        "storage.k8s.io": {"storageclasses", "volumeattachments"},
    },
}


def grants(role: dict) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for rule in role["rules"]:
        for group in rule["apiGroups"]:
            out.setdefault(group, set()).update(rule["resources"])
    return out


@pytest.mark.parametrize("role", sorted(SPECIALIST_RULES))
def test_specialist_cluster_roles_grant_exactly_what_the_tools_read(
    docs: list[dict], role: str
) -> None:
    assert grants(of_kind(docs, "ClusterRole")[role]) == SPECIALIST_RULES[role]


def test_namespaced_roles_are_narrow(docs: list[dict]) -> None:
    roles = {(d["metadata"]["namespace"], n): d for n, d in of_kind(docs, "Role").items()}
    assert set(roles) == {("kube-system", "kiroween-agents-network-coredns"),
                          ("argocd", "kiroween-agents-deploy-rollout-argocd")}
    assert roles[("kube-system", "kiroween-agents-network-coredns")]["rules"] == [
        {"apiGroups": [""], "resources": ["configmaps"], "resourceNames": ["coredns"],
         "verbs": ["get"]}]
    assert roles[("argocd", "kiroween-agents-deploy-rollout-argocd")]["rules"] == [
        {"apiGroups": ["argoproj.io"], "resources": ["applications"],
         "verbs": ["get", "list", "watch"]}]
    bindings = {(d["metadata"]["namespace"], d["roleRef"]["name"]):
                [(s["name"], s["namespace"]) for s in d["subjects"]]
                for d in of_kind(docs, "RoleBinding").values()}
    assert bindings == {
        ("kube-system", "kiroween-agents-network-coredns"): [("network-sa", NAMESPACE)],
        ("argocd", "kiroween-agents-deploy-rollout-argocd"): [("deploy-rollout-sa", NAMESPACE)],
    }


def test_specialists_have_no_access_to_argo_or_coredns_outside_their_roles(
    docs: list[dict],
) -> None:
    for role in of_kind(docs, "ClusterRole").values():
        assert "argoproj.io" not in grants(role)
        if role["metadata"]["name"] != "kiroween-agents-deploy-rollout":
            assert "configmaps" not in grants(role).get("", set())


@pytest.mark.parametrize("name,module", [("network", "kiroween_network"),
                                         ("deploy-rollout", "kiroween_deploy_rollout"),
                                         ("storage", "kiroween_storage")])
def test_specialist_deployment(docs: list[dict], name: str, module: str) -> None:
    container = of_kind(docs, "Deployment")[name]["spec"]["template"]["spec"]["containers"][0]
    assert container["args"] == ["-m", module]
    assert {p["name"]: p["containerPort"] for p in container["ports"]} == {
        "api": 8080, "metrics": 9090}
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["MODEL_ID"] == "eu.anthropic.claude-sonnet-4-6"
    service = of_kind(docs, "Service")[name]
    assert service["spec"]["selector"] == {"app.kubernetes.io/name": name}


def orchestrator_env(docs: list[dict]) -> dict[str, str]:
    container = of_kind(docs, "Deployment")["orchestrator"]["spec"]["template"]["spec"][
        "containers"][0]
    return {e["name"]: e.get("value") for e in container["env"]}


def test_orchestrator_gets_a_url_per_specialist(docs: list[dict]) -> None:
    env = orchestrator_env(docs)
    assert {k: v for k, v in env.items() if k.endswith("_URL")} == {
        "CPU_MEMORY_URL": "http://cpu-memory:8080",
        "NETWORK_URL": "http://network:8080",
        "DEPLOY_ROLLOUT_URL": "http://deploy-rollout:8080",
        "STORAGE_URL": "http://storage:8080",
    }


def test_undeployed_specialist_has_no_deployment_and_no_url() -> None:
    docs = render(f"images.orchestrator.tag={SHA}", f"images.cpuMemory.tag={SHA}")
    assert set(of_kind(docs, "Deployment")) == {"orchestrator", "orchestrator-watcher",
                                                "cpu-memory"}
    env = orchestrator_env(docs)
    assert "CPU_MEMORY_URL" in env
    assert not {"NETWORK_URL", "DEPLOY_ROLLOUT_URL", "STORAGE_URL"} & set(env)


def test_watcher_reason_sets_and_exclusions(docs: list[dict]) -> None:
    container = of_kind(docs, "Deployment")["orchestrator-watcher"]["spec"]["template"][
        "spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["WAITING_REASONS"].split(",") == [
        "ImagePullBackOff", "ErrImagePull", "CrashLoopBackOff", "CreateContainerConfigError"]
    assert env["POD_EVENT_REASONS"].split(",") == [
        "FailedScheduling", "FailedMount", "FailedAttachVolume", "Unhealthy"]
    assert env["PVC_EVENT_REASONS"] == "ProvisioningFailed"
    excluded = env["EXCLUDED_NAMESPACES"].split(",")
    assert "agents" in excluded
    assert not {"chaos-oom", "chaos-netpol", "chaos-badimage", "chaos-pvc"} & set(excluded)


@pytest.mark.parametrize("policy", ["allow-metrics-scrape", "allow-pod-identity-agent"])
def test_new_pods_get_metrics_and_pod_identity(docs: list[dict], policy: str) -> None:
    expr = of_kind(docs, "NetworkPolicy")[policy]["spec"]["podSelector"]["matchExpressions"][0]
    assert set(expr["values"]) == {"orchestrator", *SPECIALISTS}


def test_ci_markers_exist_for_every_image() -> None:
    values = (CHART / "values.yaml").read_text()
    for component in ["orchestrator", *SPECIALISTS]:
        assert re.search(rf"^\s+tag: \S+ # ci-managed:{component}$", values, re.M), component

