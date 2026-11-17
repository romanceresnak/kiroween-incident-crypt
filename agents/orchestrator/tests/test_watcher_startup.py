"""hardening TASK-03: Unhealthy startup noise and depends_on in the watcher."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from test_watcher_signals import NOW, Wall, event, make, owner, waiting_pod

from kiroween_orchestrator.watcher.main import WatcherSettings


class NotFound(Exception):
    status = 404


class Pods:
    """Pod reads the test can change between event and re-check."""

    def __init__(self) -> None:
        self.pods: dict[str, dict[str, Any]] = {}
        self.reads = 0

    def set(self, name: str, *, ready: bool, started: timedelta | None = None,
            claims: tuple[str, ...] = ()) -> None:
        status: dict[str, Any] = {
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "containerStatuses": [{"name": "server", "ready": ready, "state": {"running": {
                "startedAt": (NOW + (started or timedelta())).isoformat()}}}]}
        self.pods[name] = {
            "metadata": {"name": name, "namespace": "chaos-netpol",
                         "labels": {"pod-template-hash": "5f6"},
                         "ownerReferences": owner("ReplicaSet", "netpol-server-5f6")},
            "spec": {"volumes": [{"name": c, "persistentVolumeClaim": {"claimName": c}}
                                 for c in claims]},
            "status": status}

    def __call__(self, namespace: str, name: str) -> dict[str, Any]:
        self.reads += 1
        if name not in self.pods:
            raise NotFound(name)
        return self.pods[name]


def unhealthy(pod: str = "netpol-server-5f6-a", at: timedelta = timedelta(seconds=3)) -> dict:
    return event("Pod", pod, "Unhealthy", at=NOW + at, field_path="spec.containers{server}",
                 message="Readiness probe failed: dial tcp 10.0.1.5:8080: connect: "
                         "connection refused")


def setup(**settings: int) -> tuple[Any, Any, Wall, Pods]:
    wall, pods = Wall(), Pods()
    w, client, _ = make(WatcherSettings(**settings), wall=wall, get_pod=pods)
    return w, client, wall, pods


def test_unhealthy_for_a_ready_pod_is_dropped_at_once() -> None:
    w, client, _, pods = setup()
    pods.set("netpol-server-5f6-a", ready=True)
    assert w.handle_event(unhealthy()) == [] and w.pending() == 0
    assert client.incidents == []


def test_startup_failure_that_becomes_ready_is_never_reported() -> None:
    w, client, wall, pods = setup()
    pods.set("netpol-server-5f6-a", ready=False)  # booting: connection refused
    w.handle_event(unhealthy())
    pods.set("netpol-server-5f6-a", ready=True)  # came up a few seconds later
    wall.advance(120)
    assert w.tick() == [] and client.incidents == []


def test_still_not_ready_after_the_recheck_is_submitted_once() -> None:
    w, client, wall, pods = setup()
    pods.set("netpol-server-5f6-a", ready=False)
    w.handle_event(unhealthy())
    wall.advance(120)
    assert len(w.tick()) == 1
    assert w.tick() == []
    assert client.incidents[0].reason == "Unhealthy"


def test_recheck_waits_for_the_startup_grace_after_the_container_started() -> None:
    w, client, wall, pods = setup(unhealthy_startup_grace_s=60, unhealthy_recheck_s=30)
    pods.set("netpol-server-5f6-a", ready=False, started=timedelta(seconds=20))
    w.handle_event(unhealthy(at=timedelta(seconds=25)))
    wall.advance(79)  # started + 60 = NOW + 80
    assert w.tick() == [] and w.pending() == 1
    wall.advance(1)
    assert len(w.tick()) == 1


def test_recheck_waits_at_least_the_delay_after_the_event() -> None:
    w, client, wall, pods = setup(unhealthy_startup_grace_s=60, unhealthy_recheck_s=30)
    # The container started long ago, so the delay after the event decides.
    pods.set("netpol-server-5f6-a", ready=False, started=timedelta(seconds=-600))
    w.handle_event(unhealthy(at=timedelta(seconds=0)))
    wall.advance(29)
    assert w.tick() == []
    wall.advance(1)
    assert len(w.tick()) == 1


def test_gone_pod_is_dropped_at_recheck() -> None:
    w, client, wall, pods = setup()
    pods.set("netpol-server-5f6-a", ready=False)
    w.handle_event(unhealthy())
    del pods.pods["netpol-server-5f6-a"]
    wall.advance(120)
    assert w.tick() == [] and client.incidents == []


def test_dropped_signal_is_not_marked_seen() -> None:
    w, client, wall, pods = setup()
    pods.set("netpol-server-5f6-a", ready=False)
    w.handle_event(unhealthy())
    pods.set("netpol-server-5f6-a", ready=True)
    wall.advance(120)
    w.tick()
    # Later the same pod really breaks: it is still reported.
    pods.set("netpol-server-5f6-a", ready=False)
    w.handle_event(unhealthy(at=timedelta(seconds=300)))
    wall.advance(300)
    assert len(w.tick()) == 1


def test_one_pending_recheck_per_pod() -> None:
    w, client, wall, pods = setup()
    pods.set("netpol-server-5f6-a", ready=False)
    for i in range(10):
        w.handle_event(unhealthy(at=timedelta(seconds=3 + 10 * i)))
    assert w.pending() == 1


def test_excluded_namespace_is_not_deferred() -> None:
    w, client, wall, pods = setup()
    w.handle_event(event("Pod", "coredns-1", "Unhealthy", ns="agents"))
    assert w.pending() == 0


def test_settings_from_env() -> None:
    s = WatcherSettings.from_env({"UNHEALTHY_STARTUP_GRACE_SECONDS": "90",
                                  "UNHEALTHY_RECHECK_SECONDS": "15"})
    assert (s.unhealthy_startup_grace_s, s.unhealthy_recheck_s) == (90, 15)
    assert (WatcherSettings().unhealthy_startup_grace_s, WatcherSettings().unhealthy_recheck_s) \
        == (60, 30)


# --- depends_on (REQ-WSU-06) ---------------------------------------------------------------


def test_waiting_state_incident_lists_the_claims_the_pod_mounts() -> None:
    w, client, _ = make()
    pod = waiting_pod("ImagePullBackOff")
    pod["spec"] = {"volumes": [{"name": "d", "persistentVolumeClaim": {"claimName": "data"}},
                               {"name": "tmp", "emptyDir": {}}]}
    w.handle(pod)
    assert [(d.kind, d.name) for d in client.incidents[0].depends_on] == [
        ("PersistentVolumeClaim", "data")]


@pytest.mark.parametrize("reason", ["FailedScheduling", "FailedMount"])
def test_pod_event_incident_lists_the_claims(reason: str) -> None:
    w, client, _, pods = setup()
    pods.set("pvc-demo-6c-x", ready=False, claims=("data",))
    w.handle_event(event("Pod", "pvc-demo-6c-x", reason, ns="chaos-pvc"))
    assert [(d.kind, d.name) for d in client.incidents[0].depends_on] == [
        ("PersistentVolumeClaim", "data")]


def test_pvc_incident_has_no_depends_on() -> None:
    w, client, _ = make()
    w.handle_event(event("PersistentVolumeClaim", "data", "ProvisioningFailed", ns="chaos-pvc"))
    assert client.incidents[0].depends_on == []
