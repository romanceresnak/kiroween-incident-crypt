"""scenario-lab TASK-07: lab incidents are never suppressed across scenarios or reruns, and a
CPU-starved probe can reach the cpu-memory specialist (REQ-WT-01 to WT-03). Normal tests."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from kiroween_orchestrator import agent as agent_module
from kiroween_orchestrator.agent import SIGNAL_CATEGORIES, apply_guards
from kiroween_orchestrator.correlation import correlate
from kiroween_orchestrator.lab.scenarios import SCENARIOS
from kiroween_orchestrator.watcher.main import DEFAULT_EXCLUDED, Watcher, WatcherSettings
from kiroween_shared.models import Classification, Incident, IncidentRecord

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
STARTED = NOW - timedelta(minutes=1)
LAB_NAMESPACES = [f"lab-{n:02d}" for n in range(1, 15)]
CHART_VALUES = Path(__file__).parents[3] / "gitops" / "charts" / "agents" / "values.yaml"


class FakeClient:
    def __init__(self) -> None:
        self.incidents: list[Incident] = []
        self._lock = threading.Lock()

    def submit(self, incident: Incident) -> bool:
        with self._lock:
            self.incidents.append(incident)
        return True


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make() -> tuple[Watcher, FakeClient, Clock]:
    client, clock = FakeClient(), Clock()
    watcher = Watcher(WatcherSettings(), client, started_at=STARTED, clock=clock,
                      now=lambda: NOW)
    return watcher, client, clock


def lab_pod(namespace: str, deployment: str, reason: str = "ImagePullBackOff",
            uid: str = "u-1") -> dict[str, Any]:
    return {
        "metadata": {"name": f"{deployment}-abc12-x1", "namespace": namespace, "uid": uid,
                     "labels": {"pod-template-hash": "abc12"},
                     "ownerReferences": [{"kind": "ReplicaSet", "name": f"{deployment}-abc12",
                                          "controller": True}]},
        "status": {"containerStatuses": [{"name": "app", "state": {"waiting": {
            "reason": reason, "message": "back-off"}}}]}}


def pvc_event(namespace: str, claim: str, uid: str) -> dict[str, Any]:
    return {"metadata": {"namespace": namespace, "name": f"{claim}.1"},
            "involvedObject": {"kind": "PersistentVolumeClaim", "name": claim,
                               "namespace": namespace, "uid": uid},
            "reason": "ProvisioningFailed", "message": "storageclass not found",
            "lastTimestamp": NOW.isoformat()}


# ------------------------------------------------------ never excluded (REQ-WT-02)


def test_no_lab_namespace_is_excluded_by_default_or_by_the_chart() -> None:
    assert not set(LAB_NAMESPACES) & set(DEFAULT_EXCLUDED)
    assert not set(LAB_NAMESPACES) & WatcherSettings().excluded_namespaces
    chart = yaml.safe_load(CHART_VALUES.read_text())["watcher"]["excludedNamespaces"]
    assert not set(LAB_NAMESPACES) & set(chart)
    from_env = WatcherSettings.from_env({"EXCLUDED_NAMESPACES": ",".join(chart)})
    assert not set(LAB_NAMESPACES) & from_env.excluded_namespaces


@pytest.mark.parametrize("namespace", LAB_NAMESPACES)
def test_every_lab_namespace_is_watched(namespace: str) -> None:
    watcher, client, _ = make()
    assert len(watcher.handle(lab_pod(namespace, "web-000001"))) == 1
    (incident,) = client.incidents
    assert incident.namespace == namespace and incident.source == "watcher"


def test_the_agents_and_system_namespaces_are_still_excluded() -> None:
    watcher, client, _ = make()
    for namespace in ("agents", "monitoring", "kube-system", "argocd"):
        assert watcher.handle(lab_pod(namespace, "web-000001")) == []
    assert client.incidents == []


# ----------------------------------------- different scenarios (REQ-WT-01, first half)


def test_the_same_workload_name_and_reason_in_two_scenarios_are_both_reported() -> None:
    watcher, client, _ = make()
    first = watcher.handle(lab_pod("lab-01", "web-000001"))
    second = watcher.handle(lab_pod("lab-02", "web-000001"))
    assert len(first) == len(second) == 1 and first != second
    assert {i.namespace for i in client.incidents} == {"lab-01", "lab-02"}
    assert len({i.incident_id for i in client.incidents}) == 2


def test_the_same_pod_uid_in_two_namespaces_is_still_two_incidents() -> None:
    watcher, client, _ = make()
    watcher.handle(lab_pod("lab-08", "web-000001", uid="same"))
    watcher.handle(lab_pod("lab-11", "web-000001", uid="same"))
    assert [i.namespace for i in client.incidents] == ["lab-08", "lab-11"]


def test_every_scenario_can_fire_at_once_without_suppressing_another() -> None:
    watcher, client, _ = make()
    for scenario in SCENARIOS:
        watcher.handle(lab_pod(scenario.namespace, "web-000001", uid=scenario.id))
    assert [i.namespace for i in client.incidents] == LAB_NAMESPACES


def test_claims_in_two_scenarios_with_one_name_are_both_reported() -> None:
    watcher, client, _ = make()
    assert watcher.handle_event(pvc_event("lab-12", "data-000001", "a")) != []
    assert watcher.handle_event(pvc_event("lab-13", "data-000001", "b")) != []
    assert [i.namespace for i in client.incidents] == ["lab-12", "lab-13"]


# --------------------------------------------- a second run (REQ-WT-01, second half)


def test_a_second_run_of_the_same_scenario_inside_the_cooldown_is_reported_again() -> None:
    watcher, client, clock = make()
    assert watcher.handle(lab_pod("lab-08", "web-000001", uid="run-1"))
    clock.t += 120  # well inside the 600 s cooldown
    assert watcher.handle(lab_pod("lab-08", "web-000002", uid="run-2"))
    names = [i.workload.name for i in client.incidents]
    assert names == ["web-000001", "web-000002"]
    assert len({i.incident_id for i in client.incidents}) == 2


def test_a_claim_of_a_second_run_is_reported_again_too() -> None:
    watcher, client, clock = make()
    assert watcher.handle_event(pvc_event("lab-12", "data-000001", "a"))
    clock.t += 60
    assert watcher.handle_event(pvc_event("lab-12", "data-000002", "b"))
    assert [i.workload.name for i in client.incidents] == ["data-000001", "data-000002"]


def test_the_cooldown_still_holds_back_a_real_repeat_of_one_workload() -> None:
    """Control: nothing about the lab changed the deduplication that real clusters rely on."""
    watcher, client, clock = make()
    watcher.handle(lab_pod("lab-08", "web-000001", uid="a"))
    watcher.handle(lab_pod("lab-08", "web-000001", uid="b"))  # a new pod of the same workload
    assert len(client.incidents) == 1
    clock.t += 601
    watcher.handle(lab_pod("lab-08", "web-000001", uid="c"))
    assert len(client.incidents) == 2


def test_each_run_suffix_of_the_scenarios_is_what_makes_the_incident_new() -> None:
    from kiroween_orchestrator.lab.render import render

    names = {}
    for run in ("000001", "000002"):
        names[run] = {o["metadata"]["name"] for p in render("bad-image-tag", run)
                      for o in p.objects}
    assert names["000001"].isdisjoint(names["000002"])


# ------------------------------------------------------------------ correlation


def incident(namespace: str, name: str, iid: str,
             depends_on: list[dict] | None = None) -> Incident:
    return Incident.model_validate({
        "incident_id": iid, "source": "watcher", "reason": "Unhealthy", "namespace": namespace,
        "workload": {"kind": "Deployment", "name": name}, "observed_at": NOW,
        "depends_on": depends_on or []})


def open_record(inc: Incident, age_s: int = 10) -> IncidentRecord:
    at = NOW - timedelta(seconds=age_s)
    return IncidentRecord(incident=inc, status="processing", created_at=at, updated_at=at)


def test_an_incident_is_never_correlated_with_one_in_another_scenario() -> None:
    old = open_record(incident("lab-04", "client-000001", "a"))
    new = incident("lab-05", "client-000001", "b")
    assert correlate(new, [old], NOW, 300) is None


def test_a_second_run_in_one_namespace_is_not_attached_to_the_first_run() -> None:
    old = open_record(incident("lab-04", "client-000001", "a"))
    again = incident("lab-04", "client-000002", "b")
    assert correlate(again, [old], NOW, 300) is None


def test_two_signals_of_one_run_are_still_correlated() -> None:
    """The feature the lab relies on: a claim and the pod that mounts it are one incident."""
    claim = open_record(incident("lab-12", "data-000001", "a"))
    pod = incident("lab-12", "writer-000001", "b",
                   depends_on=[{"kind": "Deployment", "name": "data-000001"}])
    found = correlate(pod, [claim], NOW, 300)
    assert found is not None and found[1] == "depends_on"


def test_claims_with_different_run_suffixes_are_not_a_shared_dependency() -> None:
    first = open_record(incident("lab-12", "writer-000001", "a",
                                 depends_on=[{"kind": "PersistentVolumeClaim",
                                              "name": "data-000001"}]))
    second = incident("lab-12", "writer-000002", "b",
                      depends_on=[{"kind": "PersistentVolumeClaim", "name": "data-000002"}])
    assert correlate(second, [first], NOW, 300) is None


# -------------------------------------------------------- routing (REQ-WT-03)


def verdict(reason: str, category: str) -> Classification:
    probe = incident("lab-02", "crunch-000001", "r1").model_copy(update={"reason": reason})
    return apply_guards(probe, Classification(category=category, rationale="r",
                                              confidence="high"))


def test_a_probe_that_times_out_can_go_to_the_cpu_memory_specialist() -> None:
    assert verdict("Unhealthy", "cpu_memory").category == "cpu_memory"
    assert verdict("Unhealthy", "network").category == "network"
    assert verdict("Unhealthy", "deploy_rollout").category == "deploy_rollout"


def test_a_probe_failure_is_still_never_a_storage_problem() -> None:
    result = verdict("Unhealthy", "storage")
    assert result.category == "unknown" and "not an expected category" in result.rationale


def test_every_other_signal_keeps_exactly_the_categories_it_had() -> None:
    assert SIGNAL_CATEGORIES == {
        "OOMKilled": frozenset({"cpu_memory"}),
        "ImagePullBackOff": frozenset({"deploy_rollout"}),
        "ErrImagePull": frozenset({"deploy_rollout"}),
        "CreateContainerConfigError": frozenset({"deploy_rollout"}),
        "CrashLoopBackOff": frozenset({"deploy_rollout", "cpu_memory"}),
        "FailedScheduling": frozenset({"storage", "cpu_memory"}),
        "ProvisioningFailed": frozenset({"storage"}),
        "FailedMount": frozenset({"storage"}),
        "FailedAttachVolume": frozenset({"storage"}),
        "Unhealthy": frozenset({"network", "deploy_rollout", "cpu_memory"}),
    }


@pytest.mark.parametrize(("reason", "wrong"), [("OOMKilled", "network"),
                                               ("ImagePullBackOff", "cpu_memory"),
                                               ("ProvisioningFailed", "cpu_memory"),
                                               ("FailedMount", "network")])
def test_the_new_category_does_not_open_any_other_signal(reason: str, wrong: str) -> None:
    assert verdict(reason, wrong).category == "unknown"


def test_every_scenario_signal_has_a_route_to_its_agent() -> None:
    """Each scenario's expected signal can reach the specialist its card names."""
    for scenario in SCENARIOS:
        reachable = set().union(*(SIGNAL_CATEGORIES[s] for s in scenario.signals))
        assert scenario.category in reachable, (scenario.id, scenario.signals)


def test_the_classifier_prompt_tells_a_starved_server_from_a_blocked_client() -> None:
    prompt = agent_module.CLASSIFY_PROMPT
    assert "server whose own health probe times out because its CPU limit" in prompt
    assert "readiness probes that time out reaching another service" in prompt
