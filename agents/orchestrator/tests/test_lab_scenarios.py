"""scenario-lab TASK-04: the 14 scenarios (REQ-SC-01 to SC-08). Normal tests."""

from __future__ import annotations

import copy
import re
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from kiroween_orchestrator.lab import catalog
from kiroween_orchestrator.lab.render import (
    KINDS,
    LABEL_LAB,
    LABEL_RUN,
    LABEL_SCENARIO,
    SELECTOR,
    RenderError,
    mebibytes,
    millicores,
    render,
    to_yaml,
)
from kiroween_orchestrator.lab.scenarios import BY_ID, SCENARIOS, Phase, Scenario

RUN = "a1b2c3"
REPO = Path(__file__).parents[3]
IDS = [s.id for s in SCENARIOS]


def objects(scenario: str, run: str = RUN, phase: int | None = None) -> list[dict]:
    phases = render(scenario, run)
    chosen = phases if phase is None else [phases[phase]]
    return [o for p in chosen for o in p.objects]


def of_kind(scenario: str, kind: str, phase: int | None = None) -> dict[str, dict]:
    return {o["metadata"]["name"]: o for o in objects(scenario, phase=phase) if o["kind"] == kind}


def pod(deployment: dict) -> dict:
    return deployment["spec"]["template"]["spec"]


def container(deployment: dict) -> dict:
    return pod(deployment)["containers"][0]


# ---------------------------------------------------------------- the catalogue (SC-01)


def test_there_are_exactly_fourteen_scenarios_in_order() -> None:
    assert len(SCENARIOS) == 14 and len(set(IDS)) == 14
    assert [s.namespace for s in SCENARIOS] == [f"lab-{n:02d}" for n in range(1, 15)]
    assert IDS == [
        "oom-killed", "cpu-throttling", "memory-leak",
        "netpol-blocks-client", "service-selector-mismatch", "wrong-target-port",
        "dns-missing-service",
        "bad-image-tag", "crashloop-missing-config-key", "missing-secret",
        "rollout-stuck-readiness",
        "pvc-missing-storageclass", "rwx-on-ebs", "missing-configmap-mount",
    ]
    assert BY_ID == {s.id: s for s in SCENARIOS}


def test_each_specialist_answers_the_number_of_scenarios_the_brief_lists() -> None:
    by_agent: dict[str, list[str]] = {}
    for s in SCENARIOS:
        by_agent.setdefault(s.agent, []).append(s.id)
    assert {a: len(v) for a, v in by_agent.items()} == {
        "cpu-memory": 3, "network": 4, "deploy-rollout": 4, "storage": 3}
    categories = {"cpu-memory": "cpu_memory", "network": "network",
                  "deploy-rollout": "deploy_rollout", "storage": "storage"}
    assert all(s.category == categories[s.agent] for s in SCENARIOS)


def test_the_expected_signals_are_ones_the_watcher_reports() -> None:
    values = yaml.safe_load((REPO / "gitops/charts/agents/values.yaml").read_text())["watcher"]
    reported = {*values["reasons"], *values["waitingReasons"], *values["podEventReasons"],
                *values["pvcEventReasons"]}
    for s in SCENARIOS:
        assert s.signals and set(s.signals) <= reported, s.id


def test_every_scenario_has_plain_words_for_a_newcomer() -> None:
    for s in SCENARIOS:
        assert 12 <= len(s.title) <= 80 and 60 <= len(s.breaks) <= 330, s.id
        assert s.breaks.endswith("."), s.id
        # No Kubernetes-only vocabulary the card would have to explain.
        assert not re.search(r"\b(kubelet|etcd|cgroup|CRD|manifest)\b", s.breaks), s.id


def test_the_lab_namespaces_are_not_excluded_by_the_watcher() -> None:
    excluded = yaml.safe_load((REPO / "gitops/charts/agents/values.yaml").read_text())[
        "watcher"]["excludedNamespaces"]
    for s in SCENARIOS:
        assert s.namespace not in excluded


# ------------------------------------------------------- what every scenario must be (SC-03, SC-04)


@pytest.mark.parametrize("scenario", IDS)
def test_only_allowed_kinds_and_no_secret_or_rbac(scenario: str) -> None:
    for obj in objects(scenario):
        assert obj["kind"] in KINDS and obj["apiVersion"] == KINDS[obj["kind"]]
    text = yaml.safe_dump(objects(scenario))
    for word in ("kind: Secret", "kind: Role", "kind: ClusterRole", "kind: Namespace",
                 "kind: Job", "kind: DaemonSet", "kind: StatefulSet", "kind: Ingress",
                 "kind: ServiceAccount", "serviceAccountName", "hostPath", "hostNetwork",
                 "privileged: true",
                 "docker.io", "namespaceSelector", "kube-system", "agents"):
        assert word not in text, (scenario, word)


@pytest.mark.parametrize("scenario", IDS)
def test_images_limits_and_hardening(scenario: str) -> None:
    for name, dep in of_kind(scenario, "Deployment").items():
        for c in pod(dep)["containers"]:
            assert c["image"].startswith("public.ecr.aws/docker/library/"), (name, c["image"])
            limits, requests = c["resources"]["limits"], c["resources"]["requests"]
            assert millicores(limits["cpu"]) <= 200 and mebibytes(limits["memory"]) <= 128
            assert millicores(requests["cpu"]) <= millicores(limits["cpu"])
            assert mebibytes(requests["memory"]) <= mebibytes(limits["memory"])
            assert c["securityContext"] == {"allowPrivilegeEscalation": False,
                                            "readOnlyRootFilesystem": True,
                                            "capabilities": {"drop": ["ALL"]}}
        spec = pod(dep)
        assert spec["securityContext"]["runAsNonRoot"] is True
        assert spec["securityContext"]["runAsUser"] == 10001
        assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
        assert spec["automountServiceAccountToken"] is False
        assert dep["spec"]["replicas"] <= 2


@pytest.mark.parametrize("scenario", IDS)
def test_the_scenario_fits_the_namespace_quota(scenario: str) -> None:
    """The chart's ResourceQuota (gitops/charts/agents/values.yaml: lab.quota) bounds a run."""
    quota = yaml.safe_load((REPO / "gitops/charts/agents/values.yaml").read_text()).get(
        "lab", {}).get("quota", {"requests.cpu": "500m", "requests.memory": "512Mi",
                                  "limits.cpu": "1", "limits.memory": "1Gi", "pods": "10"})
    cpu = memory = pods = limit_cpu = limit_memory = 0
    for phase in render(scenario, RUN):
        phase_cpu = phase_mem = phase_pods = phase_lcpu = phase_lmem = 0
        for o in phase.objects:
            if o["kind"] != "Deployment":
                continue
            replicas = o["spec"]["replicas"] + 1  # a rolling update can run one extra pod
            for c in pod(o)["containers"]:
                phase_pods += replicas
                phase_cpu += replicas * millicores(c["resources"]["requests"]["cpu"])
                phase_mem += replicas * mebibytes(c["resources"]["requests"]["memory"])
                phase_lcpu += replicas * millicores(c["resources"]["limits"]["cpu"])
                phase_lmem += replicas * mebibytes(c["resources"]["limits"]["memory"])
        cpu, memory = max(cpu, phase_cpu), max(memory, phase_mem)
        pods, limit_cpu = max(pods, phase_pods), max(limit_cpu, phase_lcpu)
        limit_memory = max(limit_memory, phase_lmem)
    assert cpu <= millicores(str(quota["requests.cpu"]))
    assert memory <= mebibytes(str(quota["requests.memory"]))
    assert limit_cpu <= millicores(str(quota["limits.cpu"]))
    assert limit_memory <= mebibytes(str(quota["limits.memory"]))
    assert pods <= int(quota["pods"])


@pytest.mark.parametrize("scenario", IDS)
def test_names_labels_and_the_run_suffix(scenario: str) -> None:
    for phase in render(scenario, RUN):
        for o in phase.objects:
            meta = o["metadata"]
            assert meta["namespace"] == BY_ID[scenario].namespace
            assert meta["labels"][LABEL_LAB] == "true"
            assert meta["labels"][LABEL_SCENARIO] == scenario
            assert meta["labels"][LABEL_RUN] == RUN
            if o["kind"] in ("Deployment", "PersistentVolumeClaim"):
                assert meta["name"].endswith(f"-{RUN}")
            else:
                assert RUN not in meta["name"], "a Service or ConfigMap keeps its name"
            if o["kind"] == "Deployment":
                labels = o["spec"]["template"]["metadata"]["labels"]
                assert labels[LABEL_LAB] == "true" and labels[LABEL_RUN] == RUN
    assert SELECTOR == "kiroween.io/lab=true"


@pytest.mark.parametrize("scenario", IDS)
def test_a_second_run_is_a_different_workload(scenario: str) -> None:
    """The watcher's incident id and cooldown contain the workload name (REQ-SC-06)."""
    first = {o["metadata"]["name"] for o in objects(scenario, "000001")
             if o["kind"] in ("Deployment", "PersistentVolumeClaim")}
    second = {o["metadata"]["name"] for o in objects(scenario, "000002")
              if o["kind"] in ("Deployment", "PersistentVolumeClaim")}
    assert first and second and not first & second


@pytest.mark.parametrize("scenario", IDS)
def test_the_yaml_export_round_trips(scenario: str) -> None:
    phases = render(scenario, RUN)
    exported = to_yaml(scenario, RUN)
    assert len(exported) == len(phases)
    for text, phase in zip(exported, phases, strict=True):
        assert list(yaml.safe_load_all(text)) == list(phase.objects)
        assert "__NS__" not in text and "__RUN__" not in text


def test_the_catalogue_shows_no_manifests() -> None:
    public = catalog.public()
    assert [p["id"] for p in public] == IDS
    for entry in public:
        assert set(entry) == {"id", "namespace", "title", "agent", "crew", "category", "breaks",
                              "signals"}
    assert {p["crew"] for p in public} == {"Vampire", "Spider", "Zombie", "Ghost"}
    assert "phases" not in str(public) and "spec" not in str(public).replace("specialist", "")


# ------------------------------------------------- the scenarios break what they say (SC-01)


def env_of(dep: dict) -> list[dict]:
    return container(dep).get("env", [])


def test_oom_killed_allocates_far_more_than_its_limit() -> None:
    (dep,) = of_kind("oom-killed", "Deployment").values()
    code = container(dep)["command"][2]
    assert "8 << 20" in code and "sleep(0.5)" in code
    assert container(dep)["resources"]["limits"]["memory"] == "64Mi"


def test_cpu_throttling_is_a_cpu_bound_server_with_a_tiny_limit_and_a_short_probe() -> None:
    (dep,) = of_kind("cpu-throttling", "Deployment").values()
    c = container(dep)
    assert c["resources"]["limits"]["cpu"] == "20m"
    assert "range(3_000_000)" in c["command"][2] and "process_time" in c["command"][2]
    probe = c["readinessProbe"]["httpGet"]
    assert probe == {"path": "/", "port": 8080}
    assert c["readinessProbe"]["timeoutSeconds"] == 1


def test_memory_leak_grows_slowly_toward_a_smaller_limit_than_oom_killed() -> None:
    (dep,) = of_kind("memory-leak", "Deployment").values()
    code = container(dep)["command"][2]
    assert "1 << 20" in code and "sleep(1)" in code
    assert container(dep)["resources"]["limits"]["memory"] == "48Mi"


def test_netpol_admits_a_label_that_the_client_does_not_have() -> None:
    policy = of_kind("netpol-blocks-client", "NetworkPolicy")["allow-trusted"]
    allowed = policy["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"]
    assert allowed == {"app": "trusted"}
    assert policy["spec"]["podSelector"]["matchLabels"] == {"app": "server"}
    deployments = of_kind("netpol-blocks-client", "Deployment")
    client = next(d for n, d in deployments.items() if n.startswith("client-"))
    labels = client["spec"]["template"]["metadata"]["labels"]
    assert labels["app"] == "client" and labels["app"] != allowed["app"]
    assert "http://server:8080/" in container(client)["readinessProbe"]["exec"]["command"]
    assert "server" in of_kind("netpol-blocks-client", "Service")


def test_selector_mismatch_leaves_the_service_without_pods() -> None:
    (svc,) = of_kind("service-selector-mismatch", "Service").values()
    pods = [d["spec"]["template"]["metadata"]["labels"]["app"]
            for d in of_kind("service-selector-mismatch", "Deployment").values()]
    assert svc["spec"]["selector"] == {"app": "web"}
    assert "web" not in pods and "web-v2" in pods


def test_wrong_target_port_points_at_a_port_nobody_listens_on() -> None:
    (svc,) = of_kind("wrong-target-port", "Service").values()
    port = svc["spec"]["ports"][0]
    assert (port["port"], port["targetPort"]) == (8080, 9090)
    server = next(d for n, d in of_kind("wrong-target-port", "Deployment").items()
                  if n.startswith("web-"))
    assert container(server)["ports"] == [{"containerPort": 8080}]
    assert "httpd -f -p 8080" in container(server)["command"][2]


def test_dns_scenario_calls_a_name_that_is_not_a_service() -> None:
    client = next(d for n, d in of_kind("dns-missing-service", "Deployment").items()
                  if n.startswith("client-"))
    url = container(client)["readinessProbe"]["exec"]["command"][-1]
    host = re.match(r"http://([^:/]+)", url).group(1)
    assert host == "payments-api"
    assert host not in of_kind("dns-missing-service", "Service")
    assert "payments" in of_kind("dns-missing-service", "Service"), "a near miss to find"


def test_bad_image_tag_runs_a_good_revision_then_a_missing_tag() -> None:
    assert len(render("bad-image-tag", RUN)) == 2
    first, second = render("bad-image-tag", RUN)
    (a,), (b,) = first.objects, second.objects
    assert a["metadata"]["name"] == b["metadata"]["name"]
    assert container(a)["image"].endswith("python:3.12-slim")
    assert container(b)["image"].endswith("python:3.12.99-gone")
    assert first.wait_for is None and second.wait_for == f"web-{RUN}"


def test_the_crashloop_reads_a_key_the_configmap_does_not_have() -> None:
    (cm,) = of_kind("crashloop-missing-config-key", "ConfigMap").values()
    assert list(cm["data"]) == ["application.conf"]
    (dep,) = of_kind("crashloop-missing-config-key", "Deployment").values()
    script = container(dep)["command"][2]
    assert "/config/app.conf" in script and "exit 1" in script
    assert container(dep)["volumeMounts"] == [{"name": "config", "mountPath": "/config"}]
    assert pod(dep)["volumes"][0]["configMap"]["name"] == cm["metadata"]["name"]


def test_the_secret_the_container_names_is_never_created() -> None:
    (dep,) = of_kind("missing-secret", "Deployment").values()
    ref = env_of(dep)[0]["valueFrom"]["secretKeyRef"]
    assert ref == {"name": "db-credentials", "key": "password"}
    assert not [o for o in objects("missing-secret") if o["kind"] == "Secret"]
    # Only this scenario may name a Secret at all.
    for other in IDS:
        if other != "missing-secret":
            assert "secretKeyRef" not in yaml.safe_dump(objects(other)), other


def test_the_stuck_rollout_changes_only_the_readiness_path() -> None:
    first, second = render("rollout-stuck-readiness", RUN)
    (a,), (b,) = first.objects, second.objects
    assert container(a)["readinessProbe"]["httpGet"]["path"] == "/"
    assert container(b)["readinessProbe"]["httpGet"]["path"] == "/ready"
    for dep in (a, b):
        assert dep["spec"]["strategy"]["rollingUpdate"] == {"maxSurge": 1, "maxUnavailable": 0}
        assert dep["spec"]["progressDeadlineSeconds"] == 120
    assert second.wait_for == f"shop-{RUN}"
    strip = lambda d: {k: v for k, v in container(d).items() if k != "readinessProbe"}  # noqa: E731
    assert strip(a) == strip(b)


def test_the_missing_storageclass_names_a_class_that_does_not_exist() -> None:
    (claim,) = of_kind("pvc-missing-storageclass", "PersistentVolumeClaim").values()
    assert claim["spec"]["storageClassName"] == "does-not-exist"
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    (dep,) = of_kind("pvc-missing-storageclass", "Deployment").values()
    assert pod(dep)["volumes"][0]["persistentVolumeClaim"]["claimName"] == claim["metadata"]["name"]
    assert dep["spec"]["strategy"] == {"type": "Recreate"}


def test_rwx_asks_block_storage_for_a_shared_disk() -> None:
    (claim,) = of_kind("rwx-on-ebs", "PersistentVolumeClaim").values()
    assert claim["spec"]["accessModes"] == ["ReadWriteMany"]
    assert claim["spec"]["storageClassName"] == "gp2"
    (dep,) = of_kind("rwx-on-ebs", "Deployment").values()
    assert pod(dep)["volumes"][0]["persistentVolumeClaim"]["claimName"] == claim["metadata"]["name"]


def test_the_mounted_configmap_is_never_created() -> None:
    (dep,) = of_kind("missing-configmap-mount", "Deployment").values()
    volume = pod(dep)["volumes"][0]
    assert volume["configMap"] == {"name": "app-settings"}
    assert not [o for o in objects("missing-configmap-mount") if o["kind"] == "ConfigMap"]


# --------------------------------------------------- the renderer refuses unsafe scenarios


def tamper(scenario_id: str, edit) -> Scenario:  # type: ignore[no-untyped-def]
    original = BY_ID[scenario_id]
    phases = []
    for phase in original.phases:
        objs = [copy.deepcopy(o) for o in phase.objects]
        for o in objs:
            edit(o)
        phases.append(Phase(tuple(objs), phase.wait_for))
    return replace(original, phases=tuple(phases))


def deployments_only(edit):  # type: ignore[no-untyped-def]
    def wrapped(o: dict) -> None:
        if o["kind"] == "Deployment":
            edit(o)
    return wrapped


def set_image(o: dict) -> None:
    container(o)["image"] = "docker.io/library/busybox:1.36"


def privileged(o: dict) -> None:
    container(o)["securityContext"]["privileged"] = True


def host_network(o: dict) -> None:
    pod(o)["hostNetwork"] = True


def host_path(o: dict) -> None:
    pod(o)["volumes"] = [{"name": "h", "hostPath": {"path": "/"}}]


def no_limits(o: dict) -> None:
    del container(o)["resources"]["limits"]


def big_limit(o: dict) -> None:
    container(o)["resources"]["limits"]["memory"] = "4Gi"


def run_as_root(o: dict) -> None:
    pod(o)["securityContext"]["runAsUser"] = 0


def mount_token(o: dict) -> None:
    pod(o)["automountServiceAccountToken"] = True


def writable_root(o: dict) -> None:
    container(o)["securityContext"]["readOnlyRootFilesystem"] = False


def add_capability(o: dict) -> None:
    container(o)["securityContext"]["capabilities"]["add"] = ["NET_ADMIN"]


def secret_volume(o: dict) -> None:
    pod(o)["volumes"] = [{"name": "s", "secret": {"secretName": "x"}}]


def service_account(o: dict) -> None:
    pod(o)["serviceAccountName"] = "default"


@pytest.mark.parametrize("edit", [set_image, privileged, host_network, host_path, no_limits,
                                  big_limit, run_as_root, mount_token, writable_root,
                                  add_capability, secret_volume, service_account])
def test_an_unsafe_workload_is_refused(edit) -> None:  # type: ignore[no-untyped-def]
    scenario = tamper("oom-killed", deployments_only(edit))
    with pytest.raises(RenderError):
        render(scenario, RUN)


def test_a_secret_object_or_an_unknown_kind_is_refused() -> None:
    def make_secret(o: dict) -> None:
        o.update(kind="Secret", apiVersion="v1")

    with pytest.raises(RenderError, match="not allowed"):
        render(tamper("missing-configmap-mount", make_secret), RUN)

    def make_cluster_role(o: dict) -> None:
        o.update(kind="ClusterRole", apiVersion="rbac.authorization.k8s.io/v1")

    with pytest.raises(RenderError, match="not allowed"):
        render(tamper("oom-killed", make_cluster_role), RUN)


def test_the_wrong_namespace_or_api_version_is_refused() -> None:
    with pytest.raises(RenderError, match="namespace"):
        render(tamper("oom-killed", lambda o: o["metadata"].update(namespace="kube-system")), RUN)
    with pytest.raises(RenderError, match="apiVersion"):
        render(tamper("oom-killed", lambda o: o.update(apiVersion="apps/v1beta1")), RUN)


def test_a_deployment_without_the_run_suffix_is_refused() -> None:
    def no_suffix(o: dict) -> None:
        o["metadata"]["name"] = "leaker"

    with pytest.raises(RenderError, match="run suffix"):
        render(tamper("oom-killed", no_suffix), RUN)

    def suffixed_service(o: dict) -> None:
        o["metadata"]["name"] = f"web-{RUN}"

    with pytest.raises(RenderError, match="run suffix"):
        render(tamper("service-selector-mismatch",
                      lambda o: suffixed_service(o) if o["kind"] == "Service" else None), RUN)


def test_storage_is_limited_to_two_classes_and_one_gibibyte() -> None:
    with pytest.raises(RenderError, match="storage class"):
        render(tamper("rwx-on-ebs", lambda o: o["spec"].update(storageClassName="io2")
                      if o["kind"] == "PersistentVolumeClaim" else None), RUN)

    def big(o: dict) -> None:
        if o["kind"] == "PersistentVolumeClaim":
            o["spec"]["resources"]["requests"]["storage"] = "100Gi"

    with pytest.raises(RenderError, match="more than 1Gi"):
        render(tamper("rwx-on-ebs", big), RUN)


def test_a_secret_reference_is_refused_outside_the_one_scenario_that_shows_it_missing() -> None:
    def add_ref(o: dict) -> None:
        container(o)["env"] = [{"name": "X", "valueFrom": {"secretKeyRef": {"name": "s",
                                                                           "key": "k"}}}]

    with pytest.raises(RenderError, match="Secret"):
        render(tamper("oom-killed", deployments_only(add_ref)), RUN)
    render(tamper("missing-secret", deployments_only(add_ref)), RUN)  # allowed there
    with pytest.raises(RenderError, match="envFrom"):
        render(tamper("oom-killed", deployments_only(
            lambda o: container(o).update(envFrom=[{"secretRef": {"name": "s"}}]))), RUN)


def test_a_loadbalancer_service_is_refused() -> None:
    with pytest.raises(RenderError, match="ClusterIP"):
        render(tamper("wrong-target-port", lambda o: o["spec"].update(type="LoadBalancer")
                      if o["kind"] == "Service" else None), RUN)


@pytest.mark.parametrize("run", ["", "A1B2C3", "a1b2c", "a1b2c34", "../../x", "a1b2c3\n", "g1b2c3"])
def test_a_bad_run_id_is_refused(run: str) -> None:
    with pytest.raises(RenderError, match="run id"):
        render("oom-killed", run)


def test_an_unknown_scenario_id_is_refused() -> None:
    for bad in ("", "oom", "../etc/passwd", "OOM-KILLED", "oom-killed ", "lab-01"):
        with pytest.raises(RenderError, match="unknown scenario"):
            render(bad, RUN)


def test_rendering_does_not_change_the_catalogue() -> None:
    before = copy.deepcopy([s.phases for s in SCENARIOS])
    for s in SCENARIOS:
        render(s, RUN)
        render(s, "ffffff")
    assert [s.phases for s in SCENARIOS] == before
