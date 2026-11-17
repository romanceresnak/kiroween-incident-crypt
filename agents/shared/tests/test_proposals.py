"""specialists TASK-01: set_field proposal framework and model changes."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from kiroween_shared.k8s.client import KubeError
from kiroween_shared.models import Classification, Incident, ProposalDraft
from kiroween_shared.proposals import (
    LABEL_KEY_SEGMENT,
    MISSING,
    ChangeRule,
    PathError,
    incident_workload,
    label_value,
    parse_path,
    port_value,
    resolve,
    validate_change,
)


def incident(**overrides: object) -> Incident:
    data = {
        "incident_id": "i1", "source": "watcher", "reason": "Unhealthy", "namespace": "shop",
        "workload": {"kind": "Deployment", "name": "web"}, "pod": "web-abc", "container": "web",
        "observed_at": datetime(2026, 11, 17, tzinfo=UTC),
    }
    data.update(overrides)
    return Incident.model_validate(data)


SERVICE = {"kind": "Service", "metadata": {"name": "web", "namespace": "shop"},
           "spec": {"selector": {"app": "wbe"}, "ports": [{"name": "http", "port": 80,
                                                          "targetPort": 8080}]}}


class Kube:
    def __init__(self, objects: dict | None = None) -> None:
        self.objects = objects or {("Service", "shop", "web"): SERVICE}

    def _get(self, kind: str, ns: str, name: str) -> dict:
        if (kind, ns, name) not in self.objects:
            raise KubeError(404, "not_found", name)
        return self.objects[(kind, ns, name)]

    def get_service(self, ns: str, name: str) -> dict:
        return self._get("Service", ns, name)

    def get_network_policy(self, ns: str, name: str) -> dict:
        return self._get("NetworkPolicy", ns, name)

    def get_workload(self, kind: str, ns: str, name: str) -> dict:
        return self._get(kind, ns, name)

    def get_pvc(self, ns: str, name: str) -> dict:
        return self._get("PersistentVolumeClaim", ns, name)


RULES = [
    ChangeRule("Service", re.compile(r"spec\.selector" + LABEL_KEY_SEGMENT), label_value),
    ChangeRule("Service", re.compile(r"spec\.ports\[\d+\]\.targetPort"), port_value),
    ChangeRule("Deployment", re.compile(r"spec\.replicas"), lambda c, d, o: None,
               target=incident_workload),
]


def draft(**overrides: object) -> ProposalDraft:
    data: dict = {"action": "set_field",
                  "target": {"namespace": "shop", "kind": "Service", "name": "web"},
                  "field": "spec.selector.app", "current": "wbe", "proposed": "web",
                  "rationale": "selector typo", "risk": "none"}
    data.update(overrides)
    return ProposalDraft.model_validate(data)


# --- paths ------------------------------------------------------------------


@pytest.mark.parametrize("path,tokens", [
    ("spec.selector.app", ["spec", "selector", "app"]),
    ("spec.ports[0].targetPort", ["spec", "ports", 0, "targetPort"]),
    ("spec.template.spec.containers[name=web].image",
     ["spec", "template", "spec", "containers", ("name", "web"), "image"]),
    ('spec.selector["app.kubernetes.io/name"]', ["spec", "selector", "app.kubernetes.io/name"]),
])
def test_parse_path(path: str, tokens: list) -> None:
    assert parse_path(path) == tokens


@pytest.mark.parametrize("path", ["", ".spec", "spec.", "spec..a", "spec[x]", "spec selector",
                                  "spec[name=a b]"])
def test_parse_path_rejects(path: str) -> None:
    with pytest.raises(PathError):
        parse_path(path)


def test_resolve() -> None:
    assert resolve(SERVICE, parse_path("spec.ports[0].targetPort")) == 8080
    assert resolve(SERVICE, parse_path("spec.ports[name=http].port")) == 80
    assert resolve(SERVICE, parse_path("spec.selector.tier")) is MISSING
    assert resolve(SERVICE, parse_path("spec.ports[5].port")) is MISSING


# --- validate_change ----------------------------------------------------------


def test_valid_change_passes_with_approval() -> None:
    proposal, notes = validate_change(incident(), draft(), RULES, Kube())
    assert notes == []
    assert (proposal.action, proposal.proposed, proposal.requires_approval) == (
        "set_field", "web", True)
    assert "pull request against /gitops" in proposal.delivery


def test_numeric_current_value_matches() -> None:
    proposal, notes = validate_change(
        incident(), draft(field="spec.ports[0].targetPort", current="8080", proposed="8081"),
        RULES, Kube())
    assert notes == []


@pytest.mark.parametrize("overrides,reason", [
    ({"action": "adjust_resources"}, "not allowed for this specialist"),
    ({"target": None}, "needs a target"),
    ({"target": {"namespace": "other", "kind": "Service", "name": "web"}}, "not the incident's"),
    ({"field": "spec.type"}, "is not allowed"),
    ({"field": "spec.selector", "current": None}, "is not allowed"),
    ({"target": {"namespace": "shop", "kind": "Secret", "name": "x"}}, "is not allowed"),
    ({"target": {"namespace": "shop", "kind": "Service", "name": "gone"}}, "could not be read"),
    ({"current": "web2"}, "differs from the observed"),
    ({"field": "spec.selector.tier", "current": "x"}, "is not set"),
    ({"proposed": None}, "does not change"),
    ({"proposed": "wbe"}, "does not change"),
    ({"proposed": "not a label!"}, "not a valid label value"),
    ({"field": "spec.ports[0].targetPort", "current": "8080", "proposed": "70000"},
     "out of range"),
    ({"target": {"namespace": "shop", "kind": "Deployment", "name": "other"},
      "field": "spec.replicas", "current": "1", "proposed": "2"}, "incident's workload"),
])
def test_rejections_become_investigate_further(overrides: dict, reason: str) -> None:
    proposal, notes = validate_change(incident(), draft(**overrides), RULES, Kube())
    assert proposal.action == "investigate_further"
    assert proposal.target is None and proposal.proposed is None
    assert proposal.requires_approval is True
    assert any(reason in n for n in notes), notes


@pytest.mark.parametrize("namespace", ["kube-system", "argocd", "agents", "kube-public"])
def test_protected_namespaces_are_refused_even_when_the_incident_is_there(namespace: str) -> None:
    kube = Kube({("Service", namespace, "web"): SERVICE})
    target = {"namespace": namespace, "kind": "Service", "name": "web"}
    proposal, notes = validate_change(incident(namespace=namespace), draft(target=target),
                                      RULES, kube)
    assert proposal.action == "investigate_further"
    assert any("protected" in n for n in notes)


def test_absent_field_may_be_added_with_current_null() -> None:
    proposal, notes = validate_change(
        incident(), draft(field="spec.selector.tier", current=None, proposed="frontend"),
        RULES, Kube())
    assert notes == [] and proposal.proposed == "frontend"


def test_non_scalar_field_is_refused() -> None:
    rules = [ChangeRule("Service", re.compile(r"spec\.ports\[0\]"), lambda c, d, o: None)]
    _, notes = validate_change(incident(), draft(field="spec.ports[0]", current="x"), rules,
                               Kube())
    assert any("not a single value" in n for n in notes)


def test_investigate_further_passes_through() -> None:
    proposal, notes = validate_change(
        incident(), ProposalDraft(action="investigate_further", rationale="check the logs"),
        RULES, Kube())
    assert (proposal.action, proposal.rationale, notes) == ("investigate_further",
                                                            "check the logs", [])


# --- model changes ------------------------------------------------------------


def test_incident_without_pod_or_container_is_valid() -> None:
    pvc = incident(workload={"kind": "PersistentVolumeClaim", "name": "data"}, pod=None,
                   container=None)
    assert pvc.pod is None and pvc.container is None


def test_incident_pod_pattern_still_enforced() -> None:
    with pytest.raises(ValidationError):
        incident(pod="Bad_Pod")


def test_classification_confidence_defaults_to_medium() -> None:
    assert Classification(category="network", rationale="r").confidence == "medium"
    with pytest.raises(ValidationError):
        Classification(category="network", rationale="r", confidence="certain")  # type: ignore[arg-type]
