"""TASK-10 chart tests: render with helm and check the security properties.

Run with any Python that has pytest and PyYAML, and helm on PATH:
    python -m pytest gitops/charts/agents/tests
"""

from __future__ import annotations

import ipaddress
import json
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
# The one role that writes, bound in the 14 lab namespaces only (scenario-lab). It is tested
# on its own terms in the lab section at the end of this file.
LAB_RUNNER_ROLE = "kiroween-lab-runner"
IMAGE_KEYS = ["orchestrator", "cpuMemory", "network", "deployRollout", "storage", "incidentMemory"]
SPECIALISTS = ["cpu-memory", "network", "deploy-rollout", "storage"]
# Everything the orchestrator calls. incident-memory has no Kubernetes RBAC.
BACKENDS = [*SPECIALISTS, "incident-memory"]
# The two private /20 subnets the internal ALB uses (infra/modules/vpc).
ALB_CIDRS = ["10.0.16.0/20", "10.0.32.0/20"]

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
    roles = [d for d in docs if d["kind"] in ("Role", "ClusterRole")
             and d["metadata"]["name"] != LAB_RUNNER_ROLE]
    assert roles
    assert [d for d in docs if d["metadata"]["name"] == LAB_RUNNER_ROLE], "the one exception"
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
        # Reads the 14 lab namespaces by name (a cluster-scoped object needs a cluster binding).
        "kiroween-lab-runner-namespaces": [("lab-runner-sa", NAMESPACE)],
    }


def test_watcher_can_only_read_pods_events_and_one_pods_cpu_usage(docs: list[dict]) -> None:
    rules = of_kind(docs, "ClusterRole")["kiroween-agents-watcher"]["rules"]
    assert rules == [
        {"apiGroups": [""], "resources": ["pods", "events"], "verbs": ["get", "list", "watch"]},
        {"apiGroups": ["metrics.k8s.io"], "resources": ["pods"], "verbs": ["get"]},
    ]


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
    assert {"orchestrator-sa", *(f"{name}-sa" for name in BACKENDS)} <= names
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


def test_eight_deployments_with_expected_service_accounts(docs: list[dict]) -> None:
    deployments = of_kind(docs, "Deployment")
    assert {n: d["spec"]["template"]["spec"]["serviceAccountName"]
            for n, d in deployments.items()} == {
        "lab-runner": "lab-runner-sa",
        "orchestrator": "orchestrator-sa",
        "orchestrator-watcher": "orchestrator-watcher-sa",
        **{name: f"{name}-sa" for name in BACKENDS},
    }


@pytest.mark.parametrize("name", ["orchestrator", "orchestrator-watcher", "lab-runner",
                                  *BACKENDS])
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
    alb = [{"ipBlock": {"cidr": cidr}} for cidr in ALB_CIDRS]
    allowed = {
        # scenario-lab REQ-LC-05: the internal ALB, into the orchestrator only.
        "allow-alb-to-orchestrator": alb,
        "allow-watcher-to-orchestrator": [{"podSelector": {"matchLabels": {
            "app.kubernetes.io/name": "orchestrator-watcher"}}}],
        **{f"allow-orchestrator-to-{name}": [{"podSelector": {"matchLabels": {
            "app.kubernetes.io/name": "orchestrator"}}}] for name in [*BACKENDS, "lab-runner"]},
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
                          "allow-orchestrator-egress": BACKENDS,
                          "allow-orchestrator-egress-lab-runner": ["lab-runner"]}


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


def test_the_only_ip_block_ingress_is_the_alb_subnets_into_the_orchestrator(
        docs: list[dict]) -> None:
    """REQ-LC-05: no ingress from an address range except the internal ALB's private subnets,
    to the orchestrator, on the API port. Never the internet."""
    with_blocks = {name: p for name, p in of_kind(docs, "NetworkPolicy").items()
                   if any("ipBlock" in peer for peer in peers(p, "ingress"))}
    assert list(with_blocks) == ["allow-alb-to-orchestrator"]
    policy = with_blocks["allow-alb-to-orchestrator"]["spec"]
    assert policy["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": "orchestrator"}}
    assert policy["policyTypes"] == ["Ingress"]
    (rule,) = policy["ingress"]
    assert rule["ports"] == [{"protocol": "TCP", "port": 8080}]
    cidrs = [p["ipBlock"]["cidr"] for p in rule["from"]]
    assert cidrs == ALB_CIDRS
    for cidr in cidrs:
        network = ipaddress.ip_network(cidr)
        assert network.is_private and network.prefixlen >= 16, cidr
        assert "except" not in str(rule)


def test_the_alb_cidrs_are_the_private_subnets_terraform_creates() -> None:
    """The chart's values and infra/modules/vpc agree: the /20s at index 1 and 2 of the VPC."""
    variables = (REPO / "infra" / "modules" / "vpc" / "variables.tf").read_text()
    vpc = ipaddress.ip_network(re.search(r'variable "vpc_cidr".*?default\s*=\s*"([^"]+)"',
                                         variables, re.S).group(1))
    subnets = list(vpc.subnets(new_prefix=20))  # cidrsubnet(vpc, 4, n) is subnets[n]
    assert ALB_CIDRS == [str(subnets[1]), str(subnets[2])]
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    assert values["publicAccess"]["albCidrs"] == ALB_CIDRS


def test_the_target_group_binding_binds_the_orchestrator_service(docs: list[dict]) -> None:
    (binding,) = [d for d in docs if d["kind"] == "TargetGroupBinding"]
    assert binding["apiVersion"] == "elbv2.k8s.aws/v1beta1"
    spec = binding["spec"]
    assert spec["serviceRef"] == {"name": "orchestrator", "port": 8080}
    assert spec["targetType"] == "ip"
    assert re.fullmatch(r"arn:aws:elasticloadbalancing:eu-west-1:\d{12}:targetgroup/"
                        r"kiroween-prod-orch/[0-9a-f]{16}", spec["targetGroupARN"])
    assert "networking" not in spec, "security groups are Terraform's, not the controller's"


def test_the_target_group_arn_is_a_target_group_of_this_project() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    arn = values["publicAccess"]["targetGroupArn"]
    assert arn.split(":")[5].startswith("targetgroup/kiroween-prod-orch/")


def test_without_a_target_group_nothing_public_is_rendered() -> None:
    out = render("publicAccess.targetGroupArn=", "networkPolicy.enabled=true")
    assert not [d for d in out if d["kind"] == "TargetGroupBinding"]
    assert "allow-alb-to-orchestrator" not in of_kind(out, "NetworkPolicy")


def test_public_access_can_be_switched_off() -> None:
    out = render("publicAccess.enabled=false")
    assert not [d for d in out if d["kind"] == "TargetGroupBinding"]
    assert "allow-alb-to-orchestrator" not in of_kind(out, "NetworkPolicy")


def test_the_binding_is_not_rendered_without_network_policies_but_still_binds() -> None:
    out = render("networkPolicy.enabled=false")
    assert [d for d in out if d["kind"] == "TargetGroupBinding"]
    assert of_kind(out, "NetworkPolicy") == {}


def test_network_policies_can_be_disabled() -> None:
    assert of_kind(render("networkPolicy.enabled=false"), "NetworkPolicy") == {}


def test_service_monitors_on_by_default_for_every_agent(docs: list[dict]) -> None:
    monitors = of_kind(docs, "ServiceMonitor")
    assert set(monitors) == {"orchestrator", *BACKENDS}
    for name, monitor in monitors.items():
        assert monitor["spec"]["selector"] == {"matchLabels": {"app.kubernetes.io/name": name}}
        assert monitor["spec"]["endpoints"] == [
            {"port": "metrics", "path": "/metrics", "interval": "30s"}]
        assert monitor["metadata"]["annotations"][
            "argocd.argoproj.io/sync-options"] == "SkipDryRunOnMissingResource=true"
    assert of_kind(render("metrics.serviceMonitor.enabled=false"), "ServiceMonitor") == {}


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
                for d in of_kind(docs, "RoleBinding").values()
                if not d["metadata"]["namespace"].startswith("lab-")}
    assert bindings == {
        ("kube-system", "kiroween-agents-network-coredns"): [("network-sa", NAMESPACE)],
        ("argocd", "kiroween-agents-deploy-rollout-argocd"): [("deploy-rollout-sa", NAMESPACE)],
    }


def test_specialists_have_no_access_to_argo_or_coredns_outside_their_roles(
    docs: list[dict],
) -> None:
    for role in of_kind(docs, "ClusterRole").values():
        assert "argoproj.io" not in grants(role)
        if role["metadata"]["name"] not in ("kiroween-agents-deploy-rollout", LAB_RUNNER_ROLE):
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
        "INCIDENT_MEMORY_URL": "http://incident-memory:8080",
        "LAB_RUNNER_URL": "http://lab-runner:8080",
    }


def test_undeployed_specialist_has_no_deployment_and_no_url() -> None:
    # values.yaml carries CI-written tags for every image, so unset four explicitly.
    docs = render(f"images.orchestrator.tag={SHA}", f"images.cpuMemory.tag={SHA}",
                  "images.network.tag=unset", "images.deployRollout.tag=unset",
                  "images.storage.tag=unset", "images.incidentMemory.tag=unset")
    assert set(of_kind(docs, "Deployment")) == {"orchestrator", "orchestrator-watcher",
                                                "cpu-memory", "lab-runner"}
    env = orchestrator_env(docs)
    assert "CPU_MEMORY_URL" in env
    assert not {"NETWORK_URL", "DEPLOY_ROLLOUT_URL", "STORAGE_URL",
                "INCIDENT_MEMORY_URL"} & set(env)


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
    assert set(expr["values"]) == {"orchestrator", *BACKENDS}


def test_ci_markers_exist_for_every_image() -> None:
    values = (CHART / "values.yaml").read_text()
    for component in ["orchestrator", *BACKENDS]:
        assert re.search(rf"^\s+tag: \S+ # ci-managed:{component}$", values, re.M), component



# --- hardening (REQ-CHH-01) -------------------------------------------------------------


def container_env(docs: list[dict], deployment: str) -> dict[str, str]:
    container = of_kind(docs, "Deployment")[deployment]["spec"]["template"]["spec"][
        "containers"][0]
    return {e["name"]: e.get("value") for e in container["env"]}


def test_hardening_settings_reach_the_watcher_and_orchestrator(docs: list[dict]) -> None:
    watcher = container_env(docs, "orchestrator-watcher")
    assert watcher["UNHEALTHY_STARTUP_GRACE_SECONDS"] == "60"
    assert watcher["UNHEALTHY_RECHECK_SECONDS"] == "30"
    assert container_env(docs, "orchestrator")["CORRELATION_WINDOW_SECONDS"] == "300"


def test_hardening_settings_are_values() -> None:
    docs = render(*(f"images.{key}.tag={SHA}" for key in IMAGE_KEYS),
                  "watcher.unhealthyStartupGraceSeconds=90",
                  "orchestrator.correlationWindowSeconds=0")
    assert container_env(docs, "orchestrator-watcher")["UNHEALTHY_STARTUP_GRACE_SECONDS"] == "90"
    assert container_env(docs, "orchestrator")["CORRELATION_WINDOW_SECONDS"] == "0"


# --- memory-and-ui (REQ-MEM-05, MON-03, MON-04, MON-06, APR-05, UI-05) ------------------------


def container_of(docs: list[dict], deployment: str) -> dict:
    return of_kind(docs, "Deployment")[deployment]["spec"]["template"]["spec"]["containers"][0]


def test_incident_memory_deployment(docs: list[dict]) -> None:
    container = container_of(docs, "incident-memory")
    assert container["args"] == ["-m", "kiroween_incident_memory"]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert (env["KNOWLEDGE_BASE_ID"], env["TOP_K"], env["MIN_SCORE"]) == ("VIBAT27SDH", "3", "0.5")
    assert "MODEL_ID" not in env
    assert container["image"].endswith(f"kiroween/incident-memory:{SHA}")


def test_incident_memory_has_no_kubernetes_permissions(docs: list[dict]) -> None:
    for kind in ("ClusterRoleBinding", "RoleBinding"):
        for binding in of_kind(docs, kind).values():
            assert all(s["name"] != "incident-memory-sa" for s in binding["subjects"])


def test_github_token_comes_from_an_optional_secret_the_chart_never_creates(
    docs: list[dict],
) -> None:
    env = {e["name"]: e for e in container_of(docs, "orchestrator")["env"]}
    assert env["GITHUB_REPOSITORY"]["value"] == "romanceresnak/kiroween-incident-crypt"
    assert env["GITHUB_BASE_BRANCH"]["value"] == "main"
    assert env["GITHUB_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "orchestrator-github", "key": "token", "optional": True}
    assert "value" not in env["GITHUB_TOKEN"]
    assert of_kind(docs, "Secret") == {}


def test_ui_is_reachable_only_by_port_forward(docs: list[dict]) -> None:
    assert of_kind(docs, "Ingress") == {}
    for service in of_kind(docs, "Service").values():
        assert service["spec"]["type"] == "ClusterIP"


def test_watcher_excludes_monitoring(docs: list[dict]) -> None:
    env = {e["name"]: e.get("value") for e in container_of(docs, "orchestrator-watcher")["env"]}
    assert "monitoring" in env["EXCLUDED_NAMESPACES"].split(",")


def metric_names_in_code() -> set[str]:
    names: set[str] = set()
    for path in (REPO / "agents").glob("*/src/**/*.py"):
        text = path.read_text()
        for kind, name in re.findall(r"\b(Counter|Histogram)\(\s*\"([a-z_]+)\"", text):
            if kind == "Counter":
                names.add(f"{name}_total")
            else:
                names |= {f"{name}_bucket", f"{name}_count", f"{name}_sum"}
    return names


def test_dashboards_configmap_and_every_metric_exists(docs: list[dict]) -> None:
    cm = of_kind(docs, "ConfigMap")["kiroween-dashboards"]
    assert cm["metadata"]["labels"]["grafana_dashboard"] == "1"
    (name, raw), = cm["data"].items()
    assert name == "kiroween-agents.json"
    dashboard = json.loads(raw)
    titles = {p["title"] for p in dashboard["panels"]}
    for wanted in ["Cost per agent", "Cost per incident", "Tokens in / out",
                   "Incidents by category", "Investigation latency", "False-alarm"]:
        assert any(t.startswith(wanted) for t in titles), wanted
    known = metric_names_in_code()
    exprs = [t["expr"] for p in dashboard["panels"] for t in p["targets"]]
    used = {m for e in exprs for m in re.findall(r"\b([a-z_]+_(?:total|bucket|count|sum))\b", e)}
    assert used and used <= known, used - known
    for panel in dashboard["panels"]:
        assert panel["datasource"] == {"type": "prometheus", "uid": "${datasource}"}


def test_dashboards_can_be_disabled() -> None:
    assert "kiroween-dashboards" not in of_kind(
        render("metrics.dashboards.enabled=false"), "ConfigMap")


def test_agents_application_retries_until_the_crd_exists() -> None:
    app = yaml.safe_load((REPO / "gitops" / "apps" / "agents.yaml").read_text())
    retry = app["spec"]["syncPolicy"]["retry"]
    assert retry["limit"] >= 5 and retry["backoff"]["maxDuration"] == "5m"


# --- polish (REQ-RET-05, REQ-IAM-03) ----------------------------------------------------------


def test_min_score_is_a_value_and_is_always_a_string_the_pod_can_parse() -> None:
    docs = render(*(f"images.{key}.tag={SHA}" for key in IMAGE_KEYS),
                  "incidentMemory.minScore=0.65")
    env = {e["name"]: e.get("value") for e in container_of(docs, "incident-memory")["env"]}
    assert env["MIN_SCORE"] == "0.65"
    assert 0.0 <= float(env["MIN_SCORE"]) <= 1.0


def test_the_orchestrator_gets_the_learning_settings(docs: list[dict]) -> None:
    env = {e["name"]: e.get("value") for e in container_of(docs, "orchestrator")["env"]}
    assert env["LEARNING_BUCKET"] == "kiroween-prod-postmortems-255834079310"
    assert env["LEARNING_PREFIX"] == "learned/"
    assert env["KNOWLEDGE_BASE_ID"] == "VIBAT27SDH"
    assert env["KNOWLEDGE_BASE_DATA_SOURCE_ID"] == "UCADW10RQV"


def test_an_empty_data_source_turns_learning_off() -> None:
    docs = render(*(f"images.{key}.tag={SHA}" for key in IMAGE_KEYS),
                  "orchestrator.learning.dataSourceId=")
    env = {e["name"]: e.get("value") for e in container_of(docs, "orchestrator")["env"]}
    assert env["KNOWLEDGE_BASE_DATA_SOURCE_ID"] == ""   # the app then reports "not configured"


def test_setting_the_data_source_turns_learning_on_for_the_orchestrator_only() -> None:
    docs = render(*(f"images.{key}.tag={SHA}" for key in IMAGE_KEYS),
                  "orchestrator.learning.dataSourceId=DS12345678")
    env = {e["name"]: e.get("value") for e in container_of(docs, "orchestrator")["env"]}
    assert env["KNOWLEDGE_BASE_DATA_SOURCE_ID"] == "DS12345678"
    for name in ("orchestrator-watcher", "incident-memory", *SPECIALISTS):
        names = {e["name"] for e in container_of(docs, name)["env"]}
        assert not names & {"LEARNING_BUCKET", "KNOWLEDGE_BASE_DATA_SOURCE_ID"}, name


# --- login wiring (scenario-lab REQ-AU-02, AU-03, AU-09) ---------------------------------


def env_entries(docs: list[dict], deployment: str) -> dict[str, dict]:
    spec = of_kind(docs, "Deployment")[deployment]["spec"]["template"]["spec"]
    (container,) = spec["containers"]
    return {e["name"]: e for e in container["env"]}


def test_the_orchestrator_reads_the_login_from_an_optional_operator_secret(
        docs: list[dict]) -> None:
    env = env_entries(docs, "orchestrator")
    for name, key in [("AUTH_USERNAME", "username"), ("AUTH_PASSWORD", "password"),
                      ("AUTH_INTERNAL_TOKEN", "internal-token")]:
        ref = env[name]["valueFrom"]["secretKeyRef"]
        assert ref == {"name": "orchestrator-auth", "key": key, "optional": True}, name
    assert env["AUTH_REQUIRED"]["value"] == "true"
    assert env["AUTH_LAB_ONLY"]["value"] == "true"
    assert env["TRUSTED_PROXY_HOPS"]["value"] == "2"


def test_the_watcher_gets_only_the_internal_token(docs: list[dict]) -> None:
    env = env_entries(docs, "orchestrator-watcher")
    ref = env["ORCHESTRATOR_TOKEN"]["valueFrom"]["secretKeyRef"]
    assert ref == {"name": "orchestrator-auth", "key": "internal-token", "optional": True}
    assert not [n for n in env if n.startswith("AUTH_") or "PASSWORD" in n]


def test_the_chart_never_creates_a_secret_or_carries_a_credential(docs: list[dict]) -> None:
    assert not [d for d in docs if d["kind"] == "Secret"]
    values = (CHART / "values.yaml").read_text()
    template = (CHART / "templates" / "deployments.yaml").read_text()
    for text in (values, template):
        assert not re.search(r"(?i)password\s*:\s*[\"']?[A-Za-z0-9]{6,}", text.replace(
            "key: password", ""))
    assert "AUTH_PASSWORD" in template and "value:" not in re.search(
        r"name: AUTH_PASSWORD\n(?:.*\n){1,3}", template).group(0)


def test_login_cannot_be_switched_off_by_a_missing_secret(docs: list[dict]) -> None:
    """The Secret is optional for the pod, but the app fails closed without it (tested in
    agents/orchestrator/tests/test_auth.py), so the default stays required: true."""
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    assert values["auth"]["required"] is True
    assert values["auth"]["labOnly"] is True
    assert values["auth"]["secretName"] == "orchestrator-auth"
