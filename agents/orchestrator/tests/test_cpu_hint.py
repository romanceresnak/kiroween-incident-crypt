"""scenario-lab: the classifier is shown the CPU limit and usage of a failing probe's container,
so a server starved by its own limit is not taken for a network fault (REQ-WT-03). Normal tests.

What these tests prove is the input: the watcher reads the limit from the pod and the usage from
metrics, the incident carries them, and the classifier prompt shows them. Which category the
model then picks is measured on the real classifier, not here (docs/demo-results/lab)."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from kiroween_orchestrator.agent import CLASSIFY_PROMPT, apply_guards, incident_prompt
from kiroween_orchestrator.specialists import SpecialistClient
from kiroween_orchestrator.watcher.client import OrchestratorClient
from kiroween_orchestrator.watcher.detect import (
    container_resources,
    parse_cpu_millicores,
    usage_millicores_of,
)
from kiroween_orchestrator.watcher.main import Watcher, WatcherSettings
from kiroween_shared.models import Classification, ContainerResources, Incident

REPO = Path(__file__).parents[3]
LAB = REPO / "docs" / "demo-results" / "lab"
NOW = datetime(2026, 11, 18, 18, 0, tzinfo=UTC)

_spec = importlib.util.spec_from_file_location("measure_routing",
                                               REPO / "tools" / "lab" / "measure_routing.py")
assert _spec is not None and _spec.loader is not None
measure = importlib.util.module_from_spec(_spec)
sys.modules["measure_routing"] = measure
_spec.loader.exec_module(measure)


class FakeClient:
    def __init__(self) -> None:
        self.incidents: list[Incident] = []
        self._lock = threading.Lock()

    def submit(self, incident: Incident) -> bool:
        with self._lock:
            self.incidents.append(incident)
        return True


class Wall:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


def saved(name: str) -> dict[str, Any]:
    return json.loads((LAB / f"{name}.json").read_text())["record"]


def pod_for(incident: dict[str, Any], resources: dict[str, Any], *,
            ready: bool = False) -> dict[str, Any]:
    """The Pod the watcher would read for a saved incident, with the given container resources."""
    deployment = incident["workload"]["name"]
    pod = incident["pod"]
    replicaset = pod.rsplit("-", 1)[0]
    status: dict[str, Any] = {"conditions": [{"type": "Ready",
                                              "status": "True" if ready else "False"}]}
    return {"metadata": {"name": pod, "namespace": incident["namespace"],
                         "labels": {"pod-template-hash": replicaset.removeprefix(deployment + "-")},
                         "ownerReferences": [{"kind": "ReplicaSet", "name": replicaset,
                                              "controller": True}]},
            "spec": {"containers": [{"name": incident["container"], "resources": resources}]},
            "status": status}


def probe_event(incident: dict[str, Any]) -> dict[str, Any]:
    """The kubelet's Unhealthy event for a saved incident (the saved text starts 'Unhealthy: ')."""
    return {"metadata": {"namespace": incident["namespace"], "name": incident["pod"] + ".1"},
            "involvedObject": {"kind": "Pod", "name": incident["pod"],
                               "namespace": incident["namespace"], "uid": "u-1",
                               "fieldPath": f"spec.containers{{{incident['container']}}}"},
            "reason": "Unhealthy", "message": incident["message"].removeprefix("Unhealthy: "),
            "lastTimestamp": NOW.isoformat()}


def metrics(container: str, cpu: str) -> dict[str, Any]:
    return {"kind": "PodMetrics", "containers": [{"name": container, "usage": {"cpu": cpu,
                                                                                "memory": "9Mi"}}]}


def run_probe(incident: dict[str, Any], pod: dict[str, Any] | None,
              read_metrics: Any = None) -> list[Incident]:
    """Feed the watcher the kubelet event, let the grace period pass, return what it submitted."""
    wall, client = Wall(), FakeClient()
    watcher = Watcher(WatcherSettings(), client, started_at=NOW - timedelta(minutes=1),
                      clock=lambda: 0.0, now=wall, get_pod=lambda ns, name: pod,
                      get_pod_metrics=read_metrics)
    assert watcher.handle_event(probe_event(incident)) == []
    wall.advance(120)
    assert len(watcher.tick()) == 1
    return client.incidents


# ------------------------------------------------------------------- reading quantities


@pytest.mark.parametrize("value, expected", [
    ("20m", 20), ("1", 1000), ("0.5", 500), ("2", 2000), ("250u", 0), ("19739000n", 20),
    ("1500m", 1500), ("0", 0), (3, 3000), ("12.4m", 12), ("5845n", 0), ("20000u", 20),
])
def test_cpu_quantities_become_whole_millicores(value: Any, expected: int) -> None:
    assert parse_cpu_millicores(value) == expected


@pytest.mark.parametrize("value", [None, "", "m", "abc", "-5m", "nan", "inf", "1e99", "20Mi",
                                   True, "5 m", "1,5", "0x10", "2k"])
def test_a_quantity_that_is_not_a_cpu_amount_is_not_a_number(value: Any) -> None:
    assert parse_cpu_millicores(value) is None


def test_container_resources_reads_the_named_container_only() -> None:
    pod = {"spec": {"containers": [
        {"name": "side", "resources": {"limits": {"cpu": "1"}}},
        {"name": "server", "resources": {"limits": {"cpu": "20m"}, "requests": {"cpu": "10m"}}}]}}
    assert container_resources(pod, "server", 19) == ContainerResources(
        cpu_limit_millicores=20, cpu_request_millicores=10, cpu_usage_millicores=19)
    assert container_resources(pod, "other") is None


def test_a_single_container_pod_needs_no_field_path() -> None:
    pod = {"spec": {"containers": [{"name": "only", "resources": {"limits": {"cpu": "50m"}}}]}}
    assert container_resources(pod, None).cpu_limit_millicores == 50  # type: ignore[union-attr]
    two = {"spec": {"containers": [{"name": "a"}, {"name": "b"}]}}
    assert container_resources(two, None) is None


def test_nothing_known_means_no_hint_and_a_unlimited_container_still_reports_usage() -> None:
    bare = {"spec": {"containers": [{"name": "app", "resources": {}}]}}
    assert container_resources(bare, "app") is None
    assert container_resources(None, "app") is None
    assert container_resources({}, "app") is None
    assert container_resources(bare, "app", 7) == ContainerResources(cpu_usage_millicores=7)


def test_usage_comes_from_the_matching_container_of_the_metrics_object() -> None:
    both = {"containers": [{"name": "a", "usage": {"cpu": "3m"}},
                           {"name": "b", "usage": {"cpu": "250000000n"}}]}
    assert usage_millicores_of(both, "b") == 250
    assert usage_millicores_of(both, "c") is None
    assert usage_millicores_of({}, "a") is None and usage_millicores_of(None, "a") is None


# ------------------------------------------- the lab-02 record: a server starved of CPU


def test_the_saved_lab_02_incident_reaches_the_orchestrator_with_its_cpu_limit_and_usage() -> None:
    """lab-02 was answered by the Spider because the event text says 'connection refused' and
    nothing else. The watcher now adds what the pod and metrics-server say."""
    record = saved("cpu-throttling")
    incident = record["incident"]
    # The limit and request are the ones the Vampire's own saved evidence quotes from get_pod.
    evidence = {e["field"]: e["value"] for e in record["investigation"]["diagnosis"]["evidence"]
                if e["tool"] == "get_pod"}
    assert evidence["containers[0].resources.limits.cpu"] == "20m"
    pod = pod_for(incident, {"limits": {"cpu": evidence["containers[0].resources.limits.cpu"]},
                             "requests": {"cpu": evidence["containers[0].resources.requests.cpu"]}})
    # Usage: a busy server held to its limit shows as the limit (a fixture, not a measurement).
    submitted = run_probe(incident, pod, lambda ns, name: metrics(incident["container"], "20m"))
    (sent,) = submitted
    assert (sent.reason, sent.namespace, sent.pod, sent.container) == (
        "Unhealthy", "lab-02", incident["pod"], "server")
    assert sent.workload.model_dump() == incident["workload"]
    assert sent.message == incident["message"]
    assert sent.resources == ContainerResources(cpu_limit_millicores=20,
                                                cpu_request_millicores=10,
                                                cpu_usage_millicores=20)
    # The classifier is shown them, next to the event it already saw.
    prompt = incident_prompt(sent)
    assert '"cpu_limit_millicores": 20' in prompt and '"cpu_usage_millicores": 20' in prompt
    assert "connection refused" in prompt
    # And the guard lets the answer through: a probe may be a resource problem (REQ-WT-03).
    for category, kept in (("cpu_memory", "cpu_memory"), ("network", "network")):
        verdict = apply_guards(sent, Classification(category=category, rationale="r",
                                                    confidence="high"))
        assert verdict.category == kept


def test_the_netpol_case_still_reads_as_a_network_problem_from_its_own_numbers() -> None:
    """A readiness probe that times out on a client a NetworkPolicy cuts off: the container is
    idle, so the hint says so, and the guard still accepts the network answer."""
    incident = saved("netpol-blocks-client")["incident"]
    assert "download timed out" in incident["message"]
    pod = measure.pod_of(incident["workload"]["name"], "netpol-blocks-client")
    assert pod is not None
    sent = run_probe(incident, pod_for(incident, pod["spec"]["containers"][0]["resources"]),
                     lambda ns, name: metrics(incident["container"], "1m"))[0]
    res = sent.resources
    assert res is not None and res.cpu_limit_millicores == 50 and res.cpu_usage_millicores == 1
    assert res.cpu_usage_millicores < res.cpu_limit_millicores * 0.9
    assert apply_guards(sent, Classification(category="network", rationale="r",
                                             confidence="high")).category == "network"


def test_the_hint_is_the_scenarios_own_pod_spec_for_every_probe_scenario() -> None:
    """The measuring tool and the watcher read the limit through the same function."""
    seen = {}
    for scenario, _, incident in measure.load_records():
        if incident["reason"] != "Unhealthy":
            continue
        pod = measure.pod_of(incident["workload"]["name"], scenario)
        seen[scenario] = container_resources(pod, incident["container"]).cpu_limit_millicores
    assert seen == {"cpu-throttling": 20, "dns-missing-service": 50, "netpol-blocks-client": 50,
                    "rollout-stuck-readiness": 100, "service-selector-mismatch": 50,
                    "wrong-target-port": 50}


# --------------------------------------------------------------- when the data is missing


def test_without_metrics_the_hint_still_carries_the_limit_and_request() -> None:
    incident = saved("cpu-throttling")["incident"]
    pod = pod_for(incident, {"limits": {"cpu": "20m"}, "requests": {"cpu": "10m"}})

    def refuse(ns: str, name: str) -> dict[str, Any]:
        raise RuntimeError("metrics.k8s.io is not available")

    for reader in (None, refuse, lambda ns, name: {}, lambda ns, name: None,
                   lambda ns, name: metrics("some-other-container", "20m")):
        (sent,) = run_probe(incident, pod, reader)
        assert sent.resources == ContainerResources(cpu_limit_millicores=20,
                                                    cpu_request_millicores=10), reader


def test_a_pod_that_cannot_be_read_is_still_reported_without_a_hint() -> None:
    incident = saved("cpu-throttling")["incident"]
    wall, client = Wall(), FakeClient()

    def broken(ns: str, name: str) -> dict[str, Any]:
        raise RuntimeError("api server unavailable")

    watcher = Watcher(WatcherSettings(), client, started_at=NOW - timedelta(minutes=1),
                      clock=lambda: 0.0, now=wall, get_pod=broken,
                      get_pod_metrics=lambda ns, name: metrics("server", "20m"))
    watcher.handle_event(probe_event(incident))
    wall.advance(120)
    assert len(watcher.tick()) == 1
    assert client.incidents[0].resources is None


def test_only_a_probe_failure_gets_a_hint() -> None:
    wall, client = Wall(), FakeClient()
    watcher = Watcher(WatcherSettings(), client, started_at=NOW - timedelta(minutes=1),
                      clock=lambda: 0.0, now=wall,
                      get_pod_metrics=lambda ns, name: metrics("app", "20m"))
    oom = {"metadata": {"name": "leaker-1-x", "namespace": "lab-03", "uid": "u",
                        "ownerReferences": [{"kind": "ReplicaSet", "name": "leaker-1",
                                             "controller": True}],
                        "labels": {"pod-template-hash": "1"}},
           "spec": {"containers": [{"name": "app", "resources": {"limits": {"cpu": "50m"}}}]},
           "status": {"containerStatuses": [{"name": "app", "lastState": {"terminated": {
               "reason": "OOMKilled", "exitCode": 137, "finishedAt": NOW.isoformat()}}}]}}
    assert watcher.handle(oom)
    assert client.incidents[0].reason == "OOMKilled" and client.incidents[0].resources is None


# ------------------------------------------------------------- what the classifier is shown


def plain_incident() -> Incident:
    return Incident.model_validate(saved("oom-killed")["incident"])


def test_an_incident_without_a_hint_reads_exactly_as_before() -> None:
    prompt = incident_prompt(plain_incident())
    assert "resources" not in prompt and "millicores" not in prompt


def test_the_prompt_tells_the_model_how_to_use_the_numbers_and_when_not_to() -> None:
    flat = " ".join(CLASSIFY_PROMPT.split())
    assert "cpu_usage_millicores` is at or near `cpu_limit_millicores`" in flat
    assert "A small CPU limit alone does not decide it" in flat
    assert "no usage known, stays a network or rollout question" in flat
    assert "DNS, a Service address or another host" in flat
    # The existing category lines are untouched.
    assert "cpu_memory: OOMKilled containers, memory pressure, CPU throttling, resource limits" \
        in CLASSIFY_PROMPT


# ----------------------------------------------------------------- what goes over the wire


def test_a_hint_travels_from_the_watcher_to_the_orchestrator_and_to_the_specialist() -> None:
    incident = Incident.model_validate({**saved("cpu-throttling")["incident"], "resources": {
        "cpu_limit_millicores": 20, "cpu_request_millicores": 10, "cpu_usage_millicores": 20}})
    bodies: list[dict[str, Any]] = []

    def watcher_side(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(202, json={})

    assert OrchestratorClient("http://o", transport=httpx.MockTransport(watcher_side)
                              ).submit(incident)
    assert bodies[-1]["resources"] == {"cpu_limit_millicores": 20, "cpu_request_millicores": 10,
                                       "cpu_usage_millicores": 20}
    assert Incident.model_validate(bodies[-1]) == incident


async def test_a_specialist_is_sent_the_hint_only_when_there_is_one() -> None:
    sent: list[dict[str, Any]] = []

    def specialist_side(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(500, json={})  # the answer does not matter here

    client = SpecialistClient("http://s", 5.0, transport=httpx.MockTransport(specialist_side))
    base = saved("cpu-throttling")["incident"]
    for incident in (Incident.model_validate(base),
                     Incident.model_validate({**base, "resources": {"cpu_limit_millicores": 20}})):
        with pytest.raises(Exception):  # noqa: B017, PT011  the 500 is an error by design
            await client.investigate(incident)
    assert "resources" not in sent[0]
    assert sent[1]["resources"] == {"cpu_limit_millicores": 20, "cpu_request_millicores": None,
                                    "cpu_usage_millicores": None}


def test_the_watcher_does_not_send_a_field_an_older_orchestrator_would_refuse() -> None:
    """During a rolling update the watcher can be newer than the orchestrator."""
    bodies: list[dict[str, Any]] = []

    def side(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(202, json={})

    OrchestratorClient("http://o", transport=httpx.MockTransport(side)).submit(plain_incident())
    assert "resources" not in bodies[0]
    assert set(bodies[0]) == set(Incident.model_fields) - {"resources"}


# --------------------------------------------------------------------- the model's limits


@pytest.mark.parametrize("bad", [{"cpu_limit_millicores": -1}, {"cpu_usage_millicores": 10**9},
                                 {"cpu_limit_millicores": "20m"}, {"memory_limit": 5},
                                 {"cpu_limit_millicores": 1.5}])
def test_the_hint_accepts_only_bounded_whole_numbers(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011  pydantic's ValidationError is a ValueError
        ContainerResources.model_validate(bad)
