"""TASK-08: event watcher."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import httpx

from kiroween_orchestrator.watcher.client import OrchestratorClient
from kiroween_orchestrator.watcher.dedupe import Cooldown
from kiroween_orchestrator.watcher.detect import (
    incident_id,
    resolve_workload,
    terminations,
    to_incident,
)
from kiroween_orchestrator.watcher.main import ApiGone, Watcher, WatcherSettings, run
from kiroween_shared.models import Incident
from kiroween_shared.testing import OOM_DEPLOYMENT, OOM_NAMESPACE, oom_pod

NOW = datetime(2026, 11, 17, 14, 0, tzinfo=UTC)


class FakeClient:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.incidents: list[Incident] = []

    def submit(self, incident: Incident) -> bool:
        self.incidents.append(incident)
        return self.ok


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make_watcher(client: FakeClient | None = None, clock: Clock | None = None,
                 **settings: object) -> Watcher:
    return Watcher(WatcherSettings(**settings), client or FakeClient(),  # type: ignore[arg-type]
                   started_at=NOW, clock=clock or Clock(), now=lambda: NOW)


def pod_at(minutes_ago: float, *, name: str | None = None, namespace: str = OOM_NAMESPACE
           ) -> dict:
    pod = oom_pod(finished_at=(NOW - timedelta(minutes=minutes_ago)).isoformat())
    pod["metadata"]["namespace"] = namespace
    if name:
        pod["metadata"]["name"] = name
    return pod


# --- detection --------------------------------------------------------------


def test_oomkilled_status_becomes_one_incident() -> None:
    found = terminations(pod_at(1), {"OOMKilled"})
    assert len(found) == 1
    incident = to_incident(found[0], NOW)
    assert (incident.reason, incident.namespace, incident.container) == (
        "OOMKilled", OOM_NAMESPACE, "app")
    assert incident.workload.model_dump() == {"kind": "Deployment", "name": OOM_DEPLOYMENT}


def test_incident_id_is_stable_and_differs_per_termination() -> None:
    a1 = incident_id(terminations(pod_at(1), {"OOMKilled"})[0])
    a2 = incident_id(terminations(pod_at(1), {"OOMKilled"})[0])
    b = incident_id(terminations(pod_at(2), {"OOMKilled"})[0])
    assert a1 == a2 and a1 != b and len(a1) == 16


def test_other_reasons_are_ignored() -> None:
    assert terminations(pod_at(1), {"Error"}) == []


def test_replicaset_owner_resolves_to_deployment() -> None:
    assert resolve_workload(oom_pod()) == ("Deployment", OOM_DEPLOYMENT)


def test_other_owners_and_bare_pods() -> None:
    pod = oom_pod()
    pod["metadata"]["ownerReferences"] = [{"kind": "StatefulSet", "name": "db", "controller": True}]
    assert resolve_workload(pod) == ("StatefulSet", "db")
    pod["metadata"]["ownerReferences"] = []
    assert resolve_workload(pod) == ("Pod", pod["metadata"]["name"])


def test_termination_without_time_is_skipped() -> None:
    pod = oom_pod()
    del pod["status"]["containerStatuses"][0]["lastState"]["terminated"]["finishedAt"]
    assert terminations(pod, {"OOMKilled"}) == []


# --- watcher behaviour ------------------------------------------------------


def test_same_termination_seen_twice_submits_once() -> None:
    client = FakeClient()
    watcher = make_watcher(client)
    pod = pod_at(1)
    watcher.handle(pod)
    watcher.handle(copy.deepcopy(pod))
    assert len(client.incidents) == 1


def test_repeat_within_cooldown_is_suppressed_then_allowed() -> None:
    client, clock = FakeClient(), Clock()
    watcher = make_watcher(client, clock, cooldown_s=600)
    watcher.handle(pod_at(3))
    clock.t = 120
    watcher.handle(pod_at(1, name="oom-demo-7d9f8c6b5-newpod"))  # restart, new pod name
    assert len(client.incidents) == 1
    clock.t = 700
    watcher.handle(pod_at(0.5, name="oom-demo-7d9f8c6b5-third"))
    assert len(client.incidents) == 2


def test_old_termination_ignored_at_startup() -> None:
    client = FakeClient()
    make_watcher(client, startup_grace_s=300).handle(pod_at(30))
    assert client.incidents == []


def test_excluded_namespace_ignored() -> None:
    client = FakeClient()
    make_watcher(client).handle(pod_at(1, namespace="kube-system"))
    assert client.incidents == []


def test_chaos_namespace_is_not_excluded_by_default() -> None:
    assert "chaos-oom" not in WatcherSettings().excluded_namespaces
    assert "chaos-oom" not in WatcherSettings.from_env({}).excluded_namespaces


def test_settings_from_env() -> None:
    s = WatcherSettings.from_env({"WATCH_REASONS": "OOMKilled, Error", "COOLDOWN_SECONDS": "60"})
    assert s.reasons == frozenset({"OOMKilled", "Error"}) and s.cooldown_s == 60


def test_cooldown_prunes_old_keys() -> None:
    clock = Clock()
    cooldown = Cooldown(10, clock)
    assert cooldown.allow("a") and not cooldown.allow("a")
    clock.t = 11
    assert cooldown.allow("a")


# --- delivery ---------------------------------------------------------------


def transport(responses: list) -> httpx.MockTransport:  # type: ignore[type-arg]
    def handler(request: httpx.Request) -> httpx.Response:
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return httpx.Response(item, json={})
    return httpx.MockTransport(handler)


def incident() -> Incident:
    return to_incident(terminations(pod_at(1), {"OOMKilled"})[0], NOW)


def test_unreachable_orchestrator_is_retried_then_dropped() -> None:
    sleeps: list[float] = []
    errors = [httpx.ConnectError("refused")] * 3
    client = OrchestratorClient("http://o", transport=transport(errors), sleep=sleeps.append)
    assert client.submit(incident()) is False
    assert sleeps == [1.0, 2.0]


def test_recovers_after_a_transient_failure() -> None:
    client = OrchestratorClient("http://o", transport=transport([503, 202]),
                                sleep=lambda s: None)
    assert client.submit(incident()) is True


def test_rejected_incident_is_not_retried() -> None:
    responses = [422, 202]
    client = OrchestratorClient("http://o", transport=transport(responses), sleep=lambda s: None)
    assert client.submit(incident()) is False
    assert responses == [202]


# --- watch loop -------------------------------------------------------------


def test_watch_loop_lists_watches_relists_and_reconnects() -> None:
    client = FakeClient()
    watcher = make_watcher(client)
    calls: list[str] = []
    rounds = iter(range(4))

    def list_pods():  # type: ignore[no-untyped-def]
        calls.append("list")
        return [pod_at(1)], "100"

    def watch_pods(version: str):  # type: ignore[no-untyped-def]
        calls.append(f"watch@{version}")
        step = len([c for c in calls if c.startswith("watch")])
        if step == 1:
            newer = pod_at(0.5, name="oom-demo-7d9f8c6b5-other")
            newer["metadata"]["resourceVersion"] = "101"
            yield "MODIFIED", newer
            raise ApiGone()
        if step == 2:
            raise ConnectionError("dropped")

    run(watcher.handle, list_pods, watch_pods, heartbeat=lambda: None,
        should_continue=lambda: next(rounds, None) is not None, sleep=lambda s: None)

    assert calls[:4] == ["list", "watch@100", "list", "watch@100"]
    assert len(calls) >= 5 and calls[4] == "watch@100"
    assert len(client.incidents) == 1  # second pod is within the cooldown
