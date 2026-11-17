"""memory-and-ui TASK-08: the monitoring Application (kube-prometheus-stack)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

APPS = Path(__file__).parents[1] / "apps"


def app() -> dict:
    return yaml.safe_load((APPS / "monitoring.yaml").read_text())


def values() -> dict:
    return app()["spec"]["source"]["helm"]["valuesObject"]


def test_application_is_pinned_and_server_side_applied() -> None:
    spec = app()["spec"]
    assert spec["source"]["repoURL"] == "https://prometheus-community.github.io/helm-charts"
    assert (spec["source"]["chart"], spec["source"]["targetRevision"]) == (
        "kube-prometheus-stack", "92.21.2")
    assert spec["destination"]["namespace"] == "monitoring"
    options = set(spec["syncPolicy"]["syncOptions"])
    assert {"CreateNamespace=true", "ServerSideApply=true"} <= options
    assert spec["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}


def test_prometheus_is_small_and_keeps_two_days() -> None:
    p = values()["prometheus"]["prometheusSpec"]
    assert (p["retention"], p["retentionSize"]) == ("2d", "3GB")
    assert p["storageSpec"] == {"emptyDir": {"sizeLimit": "4Gi"}}
    assert p["resources"]["limits"]["memory"] == "2Gi"
    for key in ["serviceMonitorSelectorNilUsesHelmValues", "podMonitorSelectorNilUsesHelmValues",
                "ruleSelectorNilUsesHelmValues"]:
        assert p[key] is False, key


def test_unreachable_and_unused_components_are_off() -> None:
    v = values()
    for component in ["alertmanager", "kubeEtcd", "kubeControllerManager", "kubeScheduler",
                      "kubeProxy"]:
        assert v[component]["enabled"] is False, component


def test_grafana_has_no_default_password_no_anonymous_access_and_no_ingress() -> None:
    g = values()["grafana"]
    assert g["admin"] == {"existingSecret": "grafana-admin", "userKey": "admin-user",
                          "passwordKey": "admin-password"}
    assert "adminPassword" not in g
    assert g["grafana.ini"]["auth.anonymous"]["enabled"] is False
    assert g["ingress"]["enabled"] is False
    assert g["sidecar"]["dashboards"]["searchNamespace"] == "ALL"


def test_every_workload_has_requests_and_limits() -> None:
    v = values()
    for path in [("prometheusOperator",), ("prometheus", "prometheusSpec"), ("grafana",),
                 ("grafana", "sidecar"), ("kube-state-metrics",), ("prometheus-node-exporter",)]:
        node = v
        for key in path:
            node = node[key]
        assert node["resources"]["requests"] and node["resources"]["limits"], path


def mem(value: str) -> float:
    """MiB."""
    return float(value[:-2]) * (1024 if value.endswith("Gi") else 1)


def test_grafana_survives_its_startup_and_the_sidecars_their_watches() -> None:
    # OOMKilled on 17 Nov at 256Mi (Grafana) and 96Mi (each sidecar).
    g = values()["grafana"]
    assert g["resources"]["requests"]["memory"] == "256Mi"
    assert g["resources"]["limits"]["memory"] == "512Mi"
    assert g["resources"]["requests"]["cpu"] == "50m"  # CPU is unchanged
    assert g["resources"]["limits"]["cpu"] == "300m"
    assert mem(g["sidecar"]["resources"]["limits"]["memory"]) >= 192


def test_requests_do_not_exceed_limits_anywhere() -> None:
    v = values()
    for path in [("prometheusOperator",), ("prometheus", "prometheusSpec"), ("grafana",),
                 ("grafana", "sidecar"), ("kube-state-metrics",), ("prometheus-node-exporter",)]:
        node = v
        for key in path:
            node = node[key]
        r = node["resources"]
        assert mem(r["requests"]["memory"]) <= mem(r["limits"]["memory"]), path


def test_requests_fit_next_to_the_agents_on_two_t3_large() -> None:
    # 2 x (2 vCPU, ~7 GiB allocatable). The stack must leave most of it for the agents.
    def cpu(value: str) -> float:
        return float(value[:-1]) / 1000 if value.endswith("m") else float(value)

    v = values()
    requests = [v["prometheusOperator"], v["prometheus"]["prometheusSpec"], v["grafana"],
                v["grafana"]["sidecar"], v["grafana"]["sidecar"],  # dashboards + datasources
                v["kube-state-metrics"]]
    node_exporter = v["prometheus-node-exporter"]["resources"]["requests"]  # one per node
    total_cpu = sum(cpu(r["resources"]["requests"]["cpu"]) for r in requests) + 2 * cpu(
        node_exporter["cpu"])
    total_mem = sum(mem(r["resources"]["requests"]["memory"]) for r in requests) + 2 * mem(
        node_exporter["memory"])
    assert total_cpu <= 0.5 and total_mem <= 1.6 * 1024, (total_cpu, total_mem)


def test_worst_case_limits_fit_on_one_node() -> None:
    # If every container hit its limit on the same node, it still must not exhaust it.
    v = values()
    limits = [v["prometheusOperator"], v["prometheus"]["prometheusSpec"], v["grafana"],
              v["grafana"]["sidecar"], v["grafana"]["sidecar"], v["kube-state-metrics"],
              v["prometheus-node-exporter"]]
    total = sum(mem(r["resources"]["limits"]["memory"]) for r in limits)
    assert total <= 4 * 1024, total  # t3.large allocatable is about 7 GiB


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_chart_renders_with_the_application_values(tmp_path: Path) -> None:
    source = app()["spec"]["source"]
    values_file = tmp_path / "values.yaml"
    values_file.write_text(yaml.safe_dump(values()))
    result = subprocess.run(  # noqa: S603
        ["helm", "template", "monitoring", source["chart"], "--repo", source["repoURL"],  # noqa: S607
         "--version", source["targetRevision"], "-n", "monitoring", "-f", str(values_file),
         "--kube-version", "1.37.0"],
        capture_output=True, text=True, check=False)
    if result.returncode != 0 and "could not" in result.stderr.lower():
        pytest.skip(f"chart repository unreachable: {result.stderr[-200:]}")
    assert result.returncode == 0, result.stderr[-500:]
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    kinds = {d["kind"] for d in docs}
    assert "Prometheus" in kinds and "Alertmanager" not in kinds and "Ingress" not in kinds
    assert "prom-operator" not in result.stdout
    assert not [d for d in docs if d["kind"] == "Pod"]  # no helm test hook
    grafana = next(d for d in docs if d["kind"] == "Deployment"
                   and d["metadata"]["name"] == "monitoring-grafana")
    rendered = {c["name"]: c["resources"] for c in grafana["spec"]["template"]["spec"][
        "containers"]}
    assert set(rendered) == {"grafana", "grafana-sc-dashboard", "grafana-sc-datasources"}
    assert rendered["grafana"] == {"requests": {"cpu": "50m", "memory": "256Mi"},
                                   "limits": {"cpu": "300m", "memory": "512Mi"}}
    for name in ("grafana-sc-dashboard", "grafana-sc-datasources"):
        assert rendered[name]["limits"]["memory"] == "192Mi", name
    secrets = [d for d in docs if d["kind"] == "Secret"]
    assert all(not d.get("data") and not d.get("stringData") for d in secrets)
