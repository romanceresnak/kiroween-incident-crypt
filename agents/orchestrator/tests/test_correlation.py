"""hardening TASK-04: correlation of incidents with one root cause."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kiroween_orchestrator.api import create
from kiroween_orchestrator.correlation import correlate, match
from kiroween_orchestrator.service import IncidentService
from kiroween_shared.models import (
    Classification,
    Diagnosis,
    Incident,
    IncidentRecord,
    Investigation,
    Proposal,
    Usage,
)

NOW = datetime(2026, 11, 17, 14, 0, tzinfo=UTC)


def inc(iid: str, *, ns: str = "chaos-pvc", kind: str = "Deployment", name: str = "pvc-demo",
        reason: str = "FailedScheduling", pod: str | None = "pvc-demo-1",
        depends_on: list[tuple[str, str]] = ()) -> Incident:  # type: ignore[assignment]
    return Incident.model_validate({
        "incident_id": iid, "source": "watcher", "reason": reason, "namespace": ns,
        "workload": {"kind": kind, "name": name}, "pod": pod, "observed_at": NOW,
        "depends_on": [{"kind": k, "name": n} for k, n in depends_on]})


PVC = dict(kind="PersistentVolumeClaim", name="data", reason="ProvisioningFailed", pod=None)
POD = dict(depends_on=[("PersistentVolumeClaim", "data")])


class Wall:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        return self.t


class Fakes:
    def __init__(self, outcome: str = "action_required") -> None:
        self.classified: list[str] = []
        self.delegated: list[str] = []
        self.outcome = outcome

    async def classify(self, i: Incident):  # type: ignore[no-untyped-def]
        self.classified.append(i.incident_id)
        return Classification(category="storage", rationale="r"), Usage()

    async def delegate(self, i: Incident, category: str):  # type: ignore[no-untyped-def]
        self.delegated.append(i.incident_id)
        if self.outcome == "action_required":
            inv = Investigation(diagnosis=Diagnosis(root_cause="r", confidence="high"),
                                proposal=Proposal(action="investigate_further"))
        else:
            inv = Investigation(outcome=self.outcome,
                                diagnosis=Diagnosis(root_cause="r", confidence="high"))
        return inv, Usage(), "tool"


def service(fakes: Fakes, wall: Wall, **kw: Any) -> IncidentService:
    return IncidentService(classify=fakes.classify, delegate=fakes.delegate,
                           specialists=frozenset({"storage"}), now=wall, **kw)


async def run(svc: IncidentService, *incidents: Incident, wall: Wall | None = None,
              step: float = 0) -> None:
    await svc.start()
    try:
        for i in incidents:
            svc.submit(i)
            await asyncio.wait_for(svc.drain(), timeout=5)
            if wall is not None:
                wall.t += timedelta(seconds=step)
    finally:
        await svc.stop()


# --- match rules --------------------------------------------------------------------------


@pytest.mark.parametrize("new,old,rule", [
    (inc("b", reason="ImagePullBackOff"), inc("a", reason="ErrImagePull"), "same_workload"),
    (inc("b", **POD), inc("a", **PVC), "depends_on"),
    (inc("b", **PVC), inc("a", **POD), "depends_on"),
    (inc("b", name="other", **POD), inc("a", **POD), "shared_dependency"),
    (inc("b", name="other"), inc("a"), None),
    (inc("b", kind="StatefulSet"), inc("a"), None),
])
def test_match_rules(new: Incident, old: Incident, rule: str | None) -> None:
    assert match(new, old) == rule


def record(i: Incident, *, at: datetime = NOW, **kw: Any) -> IncidentRecord:
    return IncidentRecord(incident=i, created_at=at, updated_at=at, **kw)


def test_other_namespace_and_old_incidents_do_not_match() -> None:
    records = [record(inc("a", ns="other")),
               record(inc("b"), at=NOW - timedelta(seconds=301))]
    assert correlate(inc("c"), records, NOW, 300) is None
    assert correlate(inc("c"), [record(inc("b"), at=NOW - timedelta(seconds=300))],
                     NOW, 300) is not None


@pytest.mark.parametrize("closed", [
    {"status": "failed"},
    {"status": "correlated", "related_to": "z"},
    {"status": "completed", "investigation": Investigation(
        outcome="transient", diagnosis=Diagnosis(root_cause="r", confidence="high"))},
])
def test_closed_incidents_are_not_primaries(closed: dict) -> None:
    assert correlate(inc("c"), [record(inc("a"), **closed)], NOW, 300) is None


def test_oldest_open_match_is_the_primary() -> None:
    records = [record(inc("new"), at=NOW - timedelta(seconds=10)),
               record(inc("old"), at=NOW - timedelta(seconds=200))]
    primary, rule = correlate(inc("c"), records, NOW, 300)  # type: ignore[misc]
    assert primary.incident.incident_id == "old" and rule == "same_workload"


def test_window_zero_disables_correlation() -> None:
    assert correlate(inc("c"), [record(inc("a"))], NOW, 0) is None


# --- service ------------------------------------------------------------------------------


async def test_correlated_incident_is_attached_and_not_investigated() -> None:
    fakes, wall = Fakes(), Wall()
    svc = service(fakes, wall)
    await run(svc, inc("pvc", **PVC), inc("pod", **POD), wall=wall, step=5)
    assert fakes.classified == ["pvc"] and fakes.delegated == ["pvc"]
    pod = svc.store.get("pod")
    assert (pod.status, pod.related_to) == ("correlated", "pvc")
    assert pod.classification is None and pod.usage_by_agent == {}
    related = svc.store.get("pvc").related_incidents
    assert [(r.incident_id, r.reason, r.match, r.pod) for r in related] == [
        ("pod", "FailedScheduling", "depends_on", "pvc-demo-1")]


async def test_correlation_holds_while_the_primary_is_still_queued() -> None:
    fakes, wall = Fakes(), Wall()
    svc = service(fakes, wall)
    svc.submit(inc("a", reason="ErrImagePull"))     # workers not started yet
    svc.submit(inc("b", reason="ImagePullBackOff"))
    assert svc.store.get("b").status == "correlated"
    await svc.start()
    await asyncio.wait_for(svc.drain(), timeout=5)
    await svc.stop()
    assert fakes.delegated == ["a"]


async def test_transient_primary_does_not_swallow_a_later_incident() -> None:
    fakes, wall = Fakes(outcome="transient"), Wall()
    svc = service(fakes, wall)
    await run(svc, inc("a"), inc("b"), wall=wall, step=5)
    assert fakes.delegated == ["a", "b"]
    assert svc.store.get("a").status == "completed"  # REQ-COR-06: no proposal


async def test_after_the_window_a_new_investigation_runs() -> None:
    fakes, wall = Fakes(), Wall()
    svc = service(fakes, wall, correlation_window_s=60)
    await run(svc, inc("a"), inc("b"), wall=wall, step=61)
    assert fakes.delegated == ["a", "b"]


async def test_resubmission_is_idempotent_and_correlates_nothing() -> None:
    fakes, wall = Fakes(), Wall()
    svc = service(fakes, wall)
    svc.submit(inc("a"))
    record_b, created = svc.submit(inc("b", reason="Unhealthy"))
    again, created_again = svc.submit(inc("b", reason="Unhealthy"))
    assert created and not created_again and again.related_to == "a"
    assert len(svc.store.get("a").related_incidents) == 1


def test_get_incident_exposes_related_incidents_and_related_to() -> None:
    fakes, wall = Fakes(), Wall()
    svc = service(fakes, wall)
    with TestClient(create(svc)) as client:
        body = inc("pvc", **PVC).model_dump(mode="json")
        assert client.post("/incidents", json=body).status_code == 202
        assert client.post("/incidents", json=inc("pod", **POD).model_dump(mode="json")
                           ).status_code == 202
        primary = client.get("/incidents/pvc").json()
        secondary = client.get("/incidents/pod").json()
    assert [r["incident_id"] for r in primary["related_incidents"]] == ["pod"]
    assert primary["related_incidents"][0]["match"] == "depends_on"
    assert secondary["status"] == "correlated" and secondary["related_to"] == "pvc"


async def test_delegation_tool_handles_an_investigation_without_a_proposal() -> None:
    import httpx

    from kiroween_orchestrator.agent import Orchestrator
    from kiroween_orchestrator.specialists import SpecialistClient
    from kiroween_shared.config import Settings
    from kiroween_shared.metrics import PriceTable
    from kiroween_shared.testing import ScriptedModel, text, tool_call

    transient = Investigation(outcome="transient",
                              diagnosis=Diagnosis(root_cause="startup", confidence="high"))
    replies: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=transient.model_dump(mode="json"))

    def reply(messages: list, tools: list | None) -> list:  # type: ignore[type-arg]
        for message in messages:
            for block in message.get("content", []):
                if "toolResult" in block:
                    replies.append(block["toolResult"]["content"][0]["text"])
        return text("done")

    model_id = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
    client = SpecialistClient("http://storage:8080", timeout_s=1,
                              transport=httpx.MockTransport(handler))
    orch = Orchestrator(model=ScriptedModel([tool_call("investigate_storage"), reply],
                                            model_id=model_id),
                        settings=Settings(agent_name="orchestrator", model_id=model_id),
                        prices=PriceTable({}), specialists={"storage": client})
    investigation, _, mode = await orch.delegate(inc("x", **PVC), "storage")
    assert (mode, investigation.outcome, investigation.proposal) == ("tool", "transient", None)
    assert replies and '"action": "none"' in replies[0] and '"outcome": "transient"' in replies[0]
