"""scenario-lab TASK-02: the AWS Load Balancer Controller Application (REQ-LC-01, LC-03, LC-06)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).parents[2]
APPS = REPO / "gitops" / "apps"


def app() -> dict:
    return yaml.safe_load((APPS / "aws-load-balancer-controller.yaml").read_text())


def values() -> dict:
    return app()["spec"]["source"]["helm"]["valuesObject"]


def test_the_application_is_pinned_to_an_exact_chart_version() -> None:
    source = app()["spec"]["source"]
    assert source["repoURL"] == "https://aws.github.io/eks-charts"
    assert source["chart"] == "aws-load-balancer-controller"
    assert source["targetRevision"] == "3.6.3"
    assert re.fullmatch(r"\d+\.\d+\.\d+", source["targetRevision"])


def test_it_runs_in_kube_system_and_syncs_automatically() -> None:
    spec = app()["spec"]
    assert spec["destination"] == {"server": "https://kubernetes.default.svc",
                                   "namespace": "kube-system"}
    assert spec["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}
    assert {"ServerSideApply=true", "RespectIgnoreDifferences=true"} <= set(
        spec["syncPolicy"]["syncOptions"])


def test_the_controller_never_needs_the_instance_metadata_service() -> None:
    v = values()
    assert v["clusterName"] == "kiroween-prod"
    assert v["region"] == "eu-west-1"
    assert re.fullmatch(r"vpc-[0-9a-f]{17}", v["vpcId"])


def test_the_service_mutating_webhook_and_unused_features_are_off() -> None:
    v = values()
    assert v["enableServiceMutatorWebhook"] is False
    assert v["createIngressClassResource"] is False
    assert (v["enableShield"], v["enableWaf"], v["enableWafv2"]) == (False, False, False)
    assert v["replicaCount"] == 1
    assert v["defaultTargetType"] == "ip"


def test_the_service_account_is_the_one_pod_identity_is_associated_with() -> None:
    terraform = (REPO / "infra" / "modules" / "lb_controller" / "main.tf").read_text()
    default = re.search(r'variable "service_account".*?default\s*=\s*"([^"]+)"', terraform, re.S)
    namespace = re.search(r'variable "namespace".*?default\s*=\s*"([^"]+)"', terraform, re.S)
    assert default and namespace
    account = values()["serviceAccount"]
    assert account["create"] is True and account["name"] == default.group(1)
    assert app()["spec"]["destination"]["namespace"] == namespace.group(1)


def test_it_has_resource_requests_and_limits() -> None:
    resources = values()["resources"]
    assert set(resources) == {"requests", "limits"}
    assert resources["limits"]["memory"] == "256Mi"


def test_the_generated_certificate_does_not_keep_the_application_out_of_sync() -> None:
    ignored = {(d["kind"], d["name"]) for d in app()["spec"]["ignoreDifferences"]}
    assert ignored == {
        ("Secret", "aws-load-balancer-tls"),
        ("MutatingWebhookConfiguration", "aws-load-balancer-webhook"),
        ("ValidatingWebhookConfiguration", "aws-load-balancer-webhook"),
    }
    for diff in app()["spec"]["ignoreDifferences"]:
        if diff["kind"] == "Secret":
            assert diff["jsonPointers"] == ["/data"]
        else:
            assert diff["jqPathExpressions"] == [".webhooks[].clientConfig.caBundle"]


def test_the_agents_application_tolerates_a_missing_crd_on_the_first_sync() -> None:
    agents = yaml.safe_load((APPS / "agents.yaml").read_text())
    options = agents["spec"]["syncPolicy"]["syncOptions"]
    assert "SkipDryRunOnMissingResource=true" in options
    assert "CreateNamespace=true" in options
    assert agents["spec"]["syncPolicy"]["retry"]["limit"] >= 10


def test_the_applications_directory_holds_only_applications() -> None:
    """gitops/apps/README.md: only child Application manifests live here."""
    for path in sorted(APPS.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        assert doc["kind"] == "Application", path.name
        assert doc["metadata"]["namespace"] == "argocd", path.name
