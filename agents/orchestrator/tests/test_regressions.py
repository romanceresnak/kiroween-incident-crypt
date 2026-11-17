"""hardening TASK-06, REQ-REG-01: the three cases from the 17 Nov live runs.

A real Watcher feeds a real IncidentService (the watcher's client calls service.submit).
Only the cluster reads, the clock and the model calls are fakes. Each scenario must end in
exactly one investigation and no false alarm.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kiroween_orchestrator.service import IncidentService
from kiroween_orchestrator.watcher.main import Watcher, WatcherSettings
from kiroween_shared.models import (
    Classification,
    Diagnosis,
    Incident,
    Investigation,
    Proposal,
    Usage,
)

NOW = datetime(2026, 11, 17, 14, 0, tzinfo=UTC)
CATEGORY = {"Unhealthy": "network", "ErrImagePull": "deploy_rollout",
            "ImagePullBackOff": "deploy_rollout", "ProvisioningFailed": "storage",
            "FailedScheduling": "storage"}


class NotFound(Exception):
    status = 404


class World:
    """Clock, pods and the model-side fakes for one scenario."""

    def __init__(self) -> None:
        self.t = NOW
        self.pods: dict[tuple[str, str], dict[str, Any]] = {}
        self.investigated: list[Incident] = []
        svc = IncidentService(classify=self.classify, delegate=self.delegate,
                              specialists=frozenset(set(CATEGORY.values())), now=self.now)
        self.service = svc
        self.watcher = Watcher(WatcherSettings(), self, started_at=NOW - timedelta(minutes=5),
                               clock=lambda: (self.t - NOW).total_seconds(), now=self.now,
                               get_pod=self.get_pod)

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)

    # watcher -> orchestrator
    def submit(self, incident: Incident) -> bool:
        self.service.submit(incident)
        return True

    def get_pod(self, namespace: str, name: str) -> dict[str, Any]:
        if (namespace, name) not in self.pods:
            raise NotFound(name)
        return self.pods[(namespace, name)]

    async def classify(self, i: Incident):  # type: ignore[no-untyped-def]
        return Classification(category=CATEGORY[i.reason], rationale="r",
                              confidence="high"), Usage()

    async def delegate(self, i: Incident, category: str):  # type: ignore[no-untyped-def]
        self.investigated.append(i)
        inv = Investigation(diagnosis=Diagnosis(root_cause="r", confidence="high"),
                            proposal=Proposal(action="investigate_further"))
        return inv, Usage(), "tool"

    # cluster objects
    def pod(self, ns: str, name: str, deployment: str, *, ready: bool, container: str,
            started: float = 0, waiting: str | None = None,
            claims: tuple[str, ...] = ()) -> dict[str, Any]:
        rs = f"{deployment}-{name.split('-')[-2]}"
        state: dict[str, Any] = ({"waiting": {"reason": waiting, "message": "pull failed"}}
                                 if waiting else
                                 {"running": {"startedAt": (NOW + timedelta(seconds=started))
                                              .isoformat()}})
        pod = {"metadata": {"name": name, "namespace": ns, "uid": f"uid-{name}",
                            "labels": {"pod-template-hash": name.split("-")[-2]},
                            "ownerReferences": [{"kind": "ReplicaSet", "name": rs,
                                                 "controller": True}]},
               "spec": {"volumes": [{"name": c, "persistentVolumeClaim": {"claimName": c}}
                                    for c in claims]},
               "status": {"conditions": [{"type": "Ready",
                                          "status": "True" if ready else "False"}],
                          "containerStatuses": [{"name": container, "ready": ready,
                                                 "state": state}]}}
        self.pods[(ns, name)] = pod
        return pod

    def event(self, ns: str, kind: str, name: str, reason: str, message: str, *,
              at: float, container: str | None = None) -> None:
        involved = {"kind": kind, "name": name, "namespace": ns, "uid": f"uid-{name}"}
        if container:
            involved["fieldPath"] = f"spec.containers{{{container}}}"
        self.watcher.handle_event({
            "metadata": {"namespace": ns, "name": f"{name}.{reason}"},
            "involvedObject": involved, "reason": reason, "message": message,
            "lastTimestamp": (NOW + timedelta(seconds=at)).isoformat()})

    async def settle(self) -> None:
        self.advance(120)
        self.watcher.tick()
        await asyncio.wait_for(self.service.drain(), timeout=5)

    def records(self) -> list[Any]:
        return self.service.store.records()


@pytest.fixture
async def world():  # type: ignore[no-untyped-def]
    w = World()
    await w.service.start()
    yield w
    await w.service.stop()


async def test_netpol_server_startup_is_no_incident_and_the_client_is_one(world: World) -> None:
    ns = "chaos-netpol"
    world.pod(ns, "netpol-server-5f6-a", "netpol-server", ready=False, container="server")
    world.pod(ns, "netpol-client-7d9-b", "netpol-client", ready=False, container="client")
    # 17 Nov: the server's probe hit the port before httpd listened.
    world.event(ns, "Pod", "netpol-server-5f6-a", "Unhealthy",
                "Readiness probe failed: dial tcp 10.0.1.5:8080: connect: connection refused",
                at=2, container="server")
    for i in range(6):  # the blocked client fails every 10 s
        world.event(ns, "Pod", "netpol-client-7d9-b", "Unhealthy",
                    "Readiness probe failed: wget: download timed out", at=5 + 10 * i,
                    container="client")
    world.advance(8)
    world.pod(ns, "netpol-server-5f6-a", "netpol-server", ready=True, container="server")
    await world.settle()

    assert [(i.workload.name, i.reason) for i in world.investigated] == [
        ("netpol-client", "Unhealthy")]
    assert [r.incident.workload.name for r in world.records()] == ["netpol-client"]


async def test_badimage_is_one_investigation(world: World) -> None:
    ns = "chaos-badimage"
    # Revision 1 starting: one startup probe failure, then Ready.
    world.pod(ns, "badimage-demo-a1-x", "badimage-demo", ready=False, container="web")
    world.event(ns, "Pod", "badimage-demo-a1-x", "Unhealthy",
                "Readiness probe failed: connection refused", at=1, container="web")
    world.advance(5)
    world.pod(ns, "badimage-demo-a1-x", "badimage-demo", ready=True, container="web")
    # Revision 2: the new pod cannot pull its image.
    world.watcher.handle(world.pod(ns, "badimage-demo-b2-y", "badimage-demo", ready=False,
                                   container="web", waiting="ErrImagePull"))
    world.watcher.handle(world.pod(ns, "badimage-demo-b2-y", "badimage-demo", ready=False,
                                   container="web", waiting="ImagePullBackOff"))
    await world.settle()

    assert [i.reason for i in world.investigated] == ["ErrImagePull"]
    records = {r.incident.reason: r for r in world.records()}
    assert set(records) == {"ErrImagePull", "ImagePullBackOff"}
    primary = records["ErrImagePull"]
    assert [(r.reason, r.match) for r in primary.related_incidents] == [
        ("ImagePullBackOff", "same_workload")]
    assert records["ImagePullBackOff"].related_to == primary.incident.incident_id


async def test_badimage_unhealthy_that_persists_is_correlated_not_investigated(
    world: World,
) -> None:
    ns = "chaos-badimage"
    world.watcher.handle(world.pod(ns, "badimage-demo-b2-y", "badimage-demo", ready=False,
                                   container="web", waiting="ErrImagePull"))
    world.pod(ns, "badimage-demo-a1-x", "badimage-demo", ready=False, container="web")
    world.event(ns, "Pod", "badimage-demo-a1-x", "Unhealthy", "Readiness probe failed",
                at=1, container="web")
    await world.settle()
    assert [i.reason for i in world.investigated] == ["ErrImagePull"]
    unhealthy = next(r for r in world.records() if r.incident.reason == "Unhealthy")
    assert unhealthy.status == "correlated"


@pytest.mark.parametrize("pvc_first", [True, False])
async def test_pvc_is_one_investigation_in_either_order(world: World, pvc_first: bool) -> None:
    ns = "chaos-pvc"
    world.pod(ns, "pvc-demo-6c-z", "pvc-demo", ready=False, container="app", claims=("data",))

    def pvc() -> None:
        world.event(ns, "PersistentVolumeClaim", "data", "ProvisioningFailed",
                    'storageclass.storage.k8s.io "does-not-exist" not found', at=1)

    def pod() -> None:
        world.event(ns, "Pod", "pvc-demo-6c-z", "FailedScheduling",
                    "0/2 nodes are available: pod has unbound immediate PersistentVolumeClaims",
                    at=2)

    for step in ((pvc, pod) if pvc_first else (pod, pvc)):
        step()
        world.advance(3)
    await world.settle()

    assert len(world.investigated) == 1
    first = "ProvisioningFailed" if pvc_first else "FailedScheduling"
    assert world.investigated[0].reason == first
    records = world.records()
    assert sorted(r.status for r in records) == ["completed", "correlated"]
    primary = next(r for r in records if r.related_to is None)
    assert [r.match for r in primary.related_incidents] == ["depends_on"]
