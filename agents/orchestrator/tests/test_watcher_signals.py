"""specialists TASK-08: waiting states and Event signals in the watcher."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kiroween_orchestrator.watcher.detect import (
    Termination,
    incident_id,
    signal_id,
    termination_signal,
    to_incident,
)
from kiroween_orchestrator.watcher.main import Watcher, WatcherSettings
from kiroween_shared.models import Incident

NOW = datetime(2026, 11, 17, 14, 0, tzinfo=UTC)
STARTED = NOW - timedelta(minutes=1)


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


def owner(kind: str, name: str) -> list[dict[str, Any]]:
    return [{"kind": kind, "name": name, "controller": True}]


def waiting_pod(reason: str, *, ns: str = "chaos-badimage", name: str = "badimage-demo-abc12-x1",
                uid: str = "uid-1", last: str | None = None) -> dict[str, Any]:
    status: dict[str, Any] = {"name": "web", "state": {"waiting": {"reason": reason,
                                                                  "message": "back-off"}}}
    if last:
        status["lastState"] = {"terminated": {"reason": last, "exitCode": 137,
                                              "finishedAt": NOW.isoformat()}}
    return {"metadata": {"name": name, "namespace": ns, "uid": uid,
                         "labels": {"pod-template-hash": "abc12"},
                         "ownerReferences": owner("ReplicaSet", "badimage-demo-abc12")},
            "status": {"containerStatuses": [status]}}


def event(kind: str, name: str, reason: str, *, ns: str = "chaos-netpol", uid: str = "u-1",
          at: datetime = NOW, field_path: str | None = None, message: str = "m") -> dict:
    involved = {"kind": kind, "name": name, "namespace": ns, "uid": uid}
    if field_path:
        involved["fieldPath"] = field_path
    return {"metadata": {"namespace": ns, "name": f"{name}.1"}, "involvedObject": involved,
            "reason": reason, "message": message, "lastTimestamp": at.isoformat()}


def client_pod(namespace: str, name: str) -> dict[str, Any] | None:
    if name != "netpol-client-7d9-abc":
        return None
    return {"metadata": {"name": name, "namespace": namespace,
                         "labels": {"pod-template-hash": "7d9"},
                         "ownerReferences": owner("ReplicaSet", "netpol-client-7d9")}}


class Wall:
    """A wall clock the test moves forward."""

    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


def make(settings: WatcherSettings | None = None, *, wall: Wall | None = None,
         **kw: Any) -> tuple[Watcher, FakeClient, Clock]:
    client, clock = FakeClient(), Clock()
    w = Watcher(settings or WatcherSettings(), client, started_at=STARTED, clock=clock,
                now=wall or (lambda: NOW), **kw)
    return w, client, clock


# --- waiting states -----------------------------------------------------------------


@pytest.mark.parametrize("reason", ["ImagePullBackOff", "ErrImagePull", "CrashLoopBackOff",
                                    "CreateContainerConfigError"])
def test_waiting_state_is_reported_against_the_deployment(reason: str) -> None:
    w, client, _ = make()
    assert len(w.handle(waiting_pod(reason))) == 1
    inc = client.incidents[0]
    assert (inc.reason, inc.namespace, inc.workload.kind, inc.workload.name, inc.container) == (
        reason, "chaos-badimage", "Deployment", "badimage-demo", "web")
    assert inc.message.startswith(f"{reason}: ")


def test_waiting_state_is_reported_once_per_pod() -> None:
    w, client, clock = make()
    for _ in range(5):  # every status update repeats the waiting state
        w.handle(waiting_pod("ImagePullBackOff"))
    clock.t += 10_000  # even after the cooldown, the same pod is not reported again
    w.handle(waiting_pod("ImagePullBackOff"))
    assert len(client.incidents) == 1


def test_new_pod_of_same_workload_is_held_by_cooldown() -> None:
    w, client, clock = make()
    w.handle(waiting_pod("ImagePullBackOff", uid="a"))
    w.handle(waiting_pod("ImagePullBackOff", uid="b"))
    assert len(client.incidents) == 1
    clock.t += 601
    w.handle(waiting_pod("ImagePullBackOff", uid="c"))
    assert len(client.incidents) == 2


def test_crash_loop_after_oom_is_not_a_second_incident() -> None:
    w, client, _ = make()
    pod = waiting_pod("CrashLoopBackOff", last="OOMKilled")
    pod["status"]["containerStatuses"][0]["lastState"]["terminated"]["finishedAt"] = (
        NOW.isoformat())
    w.handle(pod)
    assert [i.reason for i in client.incidents] == ["OOMKilled"]


def test_crash_loop_after_an_error_exit_is_reported() -> None:
    w, client, _ = make()
    w.handle(waiting_pod("CrashLoopBackOff", last="Error"))
    assert [i.reason for i in client.incidents] == ["CrashLoopBackOff"]


def test_excluded_namespace_waiting_state_is_ignored() -> None:
    w, client, _ = make()
    w.handle(waiting_pod("ImagePullBackOff", ns="agents"))
    w.handle(waiting_pod("ImagePullBackOff", ns="kube-system"))
    assert client.incidents == []


# --- events ---------------------------------------------------------------------------


def test_readiness_event_is_resolved_to_the_deployment_and_container() -> None:
    wall = Wall()
    w, client, _ = make(get_pod=client_pod, wall=wall)
    e = event("Pod", "netpol-client-7d9-abc", "Unhealthy",
              field_path="spec.containers{client}",
              message="Readiness probe failed: wget: download timed out")
    # hardening REQ-WSU-01: held for a re-check, then submitted because still not Ready.
    assert w.handle_event(e) == [] and w.pending() == 1
    wall.advance(61)
    assert len(w.tick()) == 1
    inc = client.incidents[0]
    assert (inc.reason, inc.workload.kind, inc.workload.name, inc.pod, inc.container) == (
        "Unhealthy", "Deployment", "netpol-client", "netpol-client-7d9-abc", "client")
    assert "Readiness probe failed" in inc.message


def test_pvc_provisioning_failed_has_no_pod_or_container() -> None:
    w, client, _ = make()
    w.handle_event(event("PersistentVolumeClaim", "data", "ProvisioningFailed", ns="chaos-pvc",
                         message='storageclass.storage.k8s.io "does-not-exist" not found'))
    inc = client.incidents[0]
    assert (inc.workload.kind, inc.workload.name, inc.pod, inc.container) == (
        "PersistentVolumeClaim", "data", None, None)


@pytest.mark.parametrize("reason", ["FailedScheduling", "FailedMount", "FailedAttachVolume"])
def test_pod_events_are_reported(reason: str) -> None:
    w, client, _ = make(get_pod=client_pod)
    w.handle_event(event("Pod", "gone-pod", reason))
    # A pod that cannot be read is reported as its own workload.
    assert [(i.reason, i.workload.kind, i.workload.name) for i in client.incidents] == [
        (reason, "Pod", "gone-pod")]


@pytest.mark.parametrize("kind,reason", [("Pod", "Pulled"), ("Pod", "BackOff"),
                                         ("Node", "Unhealthy"),
                                         ("PersistentVolumeClaim", "FailedMount")])
def test_other_events_are_ignored(kind: str, reason: str) -> None:
    w, client, _ = make()
    assert w.handle_event(event(kind, "x", reason)) == []
    assert client.incidents == []


def test_repeated_event_counts_are_one_incident() -> None:
    wall = Wall()
    w, client, _ = make(get_pod=client_pod, wall=wall)
    for i in range(4):  # the kubelet bumps count and lastTimestamp on every failure
        w.handle_event(event("Pod", "netpol-client-7d9-abc", "Unhealthy",
                             at=NOW + timedelta(seconds=10 * i)))
    assert w.pending() == 1
    wall.advance(61)
    w.tick()
    w.handle_event(event("Pod", "netpol-client-7d9-abc", "Unhealthy",
                         at=NOW + timedelta(seconds=70)))
    assert w.pending() == 0  # already reported
    assert len(client.incidents) == 1


def test_old_event_is_ignored_at_startup() -> None:
    w, client, _ = make()
    old = STARTED - timedelta(seconds=WatcherSettings().startup_grace_s + 1)
    w.handle_event(event("PersistentVolumeClaim", "data", "ProvisioningFailed", at=old))
    assert client.incidents == []


def test_event_in_excluded_namespace_is_ignored() -> None:
    w, client, _ = make()
    w.handle_event(event("Pod", "coredns-1", "Unhealthy", ns="kube-system"))
    assert client.incidents == []


def test_pod_lookup_error_falls_back_to_the_pod() -> None:
    def boom(namespace: str, name: str) -> dict[str, Any] | None:
        raise RuntimeError("forbidden")

    wall = Wall()
    w, client, _ = make(get_pod=boom, wall=wall)
    w.handle_event(event("Pod", "p", "Unhealthy"))
    wall.advance(61)
    w.tick()  # unreadable is reported, not lost
    assert client.incidents[0].workload.kind == "Pod"


def test_reason_sets_come_from_env() -> None:
    s = WatcherSettings.from_env({"WAITING_REASONS": "ImagePullBackOff",
                                  "POD_EVENT_REASONS": "Unhealthy", "PVC_EVENT_REASONS": ""})
    assert s.waiting_reasons == {"ImagePullBackOff"}
    assert s.pod_event_reasons == {"Unhealthy"}
    assert "agents" in s.excluded_namespaces


# --- compatibility and concurrency ----------------------------------------------------------


def test_oom_incident_is_unchanged() -> None:
    t = Termination(namespace="chaos-oom", workload_kind="Deployment", workload_name="oom-demo",
                    pod="oom-demo-1", container="app", reason="OOMKilled", exit_code=137,
                    finished_at=NOW)
    assert signal_id(termination_signal(t)) == incident_id(t)
    old = to_incident(t, NOW)
    from kiroween_orchestrator.watcher.detect import signal_to_incident
    assert signal_to_incident(termination_signal(t), NOW) == old


def test_pod_and_event_threads_share_dedup() -> None:
    wall = Wall()
    w, client, _ = make(get_pod=client_pod, wall=wall)
    pods = [waiting_pod("ImagePullBackOff") for _ in range(200)]
    events = [event("Pod", "netpol-client-7d9-abc", "Unhealthy") for _ in range(200)]
    threads = [threading.Thread(target=lambda: [w.handle(p) for p in pods]),
               threading.Thread(target=lambda: [w.handle_event(e) for e in events]),
               threading.Thread(target=lambda: [w.tick() for _ in range(200)])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall.advance(61)
    w.tick()
    assert sorted(i.reason for i in client.incidents) == ["ImagePullBackOff", "Unhealthy"]
