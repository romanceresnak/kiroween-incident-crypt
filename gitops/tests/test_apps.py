"""TASK-11: Argo CD Application manifests in gitops/apps."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

APPS = Path(__file__).parents[1] / "apps"
REPO_URL = "https://github.com/romanceresnak/kiroween-incident-crypt"


def load(name: str) -> dict:
    return yaml.safe_load((APPS / name).read_text())


def test_apps_directory_holds_only_applications() -> None:
    for path in APPS.glob("*.y*ml"):
        doc = load(path.name)
        assert (doc["apiVersion"], doc["kind"]) == ("argoproj.io/v1alpha1", "Application")
        assert doc["metadata"]["namespace"] == "argocd"


def test_agents_application() -> None:
    app = load("agents.yaml")
    spec = app["spec"]
    assert app["metadata"]["name"] == "agents"
    assert spec["source"] == {"repoURL": REPO_URL, "targetRevision": "main",
                              "path": "gitops/charts/agents"}
    assert spec["destination"]["namespace"] == "agents"
    assert spec["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}
    assert "CreateNamespace=true" in spec["syncPolicy"]["syncOptions"]
    assert (APPS.parent / "charts" / "agents" / "Chart.yaml").exists()


def test_metrics_server_application_is_pinned() -> None:
    spec = load("metrics-server.yaml")["spec"]
    source = spec["source"]
    assert source["chart"] == "metrics-server"
    assert source["repoURL"] == "https://kubernetes-sigs.github.io/metrics-server/"
    assert source["targetRevision"] == "3.14.0"
    assert spec["destination"]["namespace"] == "kube-system"
    assert source["helm"]["valuesObject"]["replicas"] == 1
    assert source["helm"]["valuesObject"]["resources"]["limits"]
    assert spec["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_metrics_server_chart_renders_with_the_application_values(tmp_path: Path) -> None:
    source = load("metrics-server.yaml")["spec"]["source"]
    values = tmp_path / "values.yaml"
    values.write_text(yaml.safe_dump(source["helm"]["valuesObject"]))
    out = subprocess.run(  # noqa: S603
        ["helm", "template", "metrics-server", source["chart"], "--repo", source["repoURL"],  # noqa: S607
         "--version", source["targetRevision"], "--namespace", "kube-system", "-f", str(values)],
        check=True, capture_output=True, text=True, timeout=120,
    ).stdout
    docs = [d for d in yaml.safe_load_all(out) if d]
    kinds = {d["kind"] for d in docs}
    assert {"Deployment", "APIService", "ServiceAccount"} <= kinds
    api = next(d for d in docs if d["kind"] == "APIService")
    assert api["metadata"]["name"] == "v1beta1.metrics.k8s.io"
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    assert deployment["spec"]["replicas"] == 1
