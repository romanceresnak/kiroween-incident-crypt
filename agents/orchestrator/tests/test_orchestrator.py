"""TASK-07: orchestrator service, agent and API."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from kiroween_orchestrator.agent import Orchestrator
from kiroween_orchestrator.api import create
from kiroween_orchestrator.service import IncidentService, QueueFull
from kiroween_orchestrator.specialists import SpecialistClient, SpecialistError
from kiroween_orchestrator.store import IncidentStore
from kiroween_shared.config import Settings
from kiroween_shared.metrics import PriceTable
from kiroween_shared.models import (
    Classification,
    Diagnosis,
    Incident,
    IncidentRecord,
    Investigation,
    Proposal,
    Target,
    Usage,
)
from kiroween_shared.testing import ScriptedModel, text, tool_call

MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
SETTINGS = Settings(agent_name="orchestrator", model_id=MODEL)


def make_incident(incident_id: str = "inc-1", reason: str = "OOMKilled") -> Incident:
    return Incident.model_validate({
        "incident_id": incident_id, "source": "watcher", "reason": reason,
        "namespace": "chaos-oom", "workload": {"kind": "Deployment", "name": "oom-demo"},
        "pod": "oom-demo-abc", "container": "app", "message": "",
        "observed_at": datetime(2026, 11, 17, tzinfo=UTC),
    })


def investigation(action: str = "adjust_resources") -> Investigation:
    target = Target(namespace="chaos-oom", kind="Deployment", name="oom-demo", container="app")
    proposal = (
        Proposal(action="adjust_resources", target=target, field="resources.limits.memory",
                 current="64Mi", proposed="128Mi", rationale="r", risk="k")
        if action == "adjust_resources" else Proposal(action=action)  # type: ignore[arg-type]
    )
    return Investigation(
        diagnosis=Diagnosis(root_cause="limit too low", confidence="high"),
        proposal=proposal,
        usage=Usage(input_tokens=900, output_tokens=200, cost_usd=0.006),
    )


class Fakes:
    def __init__(self, category: str = "cpu_memory", action: str = "adjust_resources") -> None:
        self.category = category
        self.action = action
        self.classify_calls = 0
        self.delegate_calls = 0

    async def classify(self, incident: Incident):  # type: ignore[no-untyped-def]
        self.classify_calls += 1
        return Classification(category=self.category, rationale="r"), Usage(input_tokens=50)

    async def delegate(self, incident: Incident, category: str = "cpu_memory"):  # type: ignore[no-untyped-def]
        self.delegate_calls += 1
        return investigation(self.action), Usage(input_tokens=60), "tool"


async def run_one(service: IncidentService, incident: Incident) -> IncidentRecord:
    await service.start()
    try:
        service.submit(incident)
        await asyncio.wait_for(service.drain(), timeout=5)
    finally:
        await service.stop()
    record = service.store.get(incident.incident_id)
    assert record is not None
    return record


# --- service ----------------------------------------------------------------


async def test_cpu_memory_routes_to_specialist_and_awaits_approval() -> None:
    fakes = Fakes()
    record = await run_one(IncidentService(classify=fakes.classify, delegate=fakes.delegate),
                           make_incident())
    assert record.status == "awaiting_approval"
    assert record.classification and record.classification.category == "cpu_memory"
    assert record.investigation and record.investigation.proposal.requires_approval is True
    assert record.delegation == "tool"
    assert record.usage_by_agent["orchestrator"].input_tokens == 110
    assert record.usage_by_agent["cpu-memory"].cost_usd == pytest.approx(0.006)
    assert fakes.delegate_calls == 1


async def test_proposal_without_change_completes() -> None:
    fakes = Fakes(action="investigate_further")
    record = await run_one(IncidentService(classify=fakes.classify, delegate=fakes.delegate),
                           make_incident())
    assert record.status == "completed"


@pytest.mark.parametrize("category", ["network", "deploy_rollout", "storage", "unknown"])
async def test_category_without_specialist(category: str) -> None:
    fakes = Fakes(category=category)
    record = await run_one(IncidentService(classify=fakes.classify, delegate=fakes.delegate),
                           make_incident())
    assert record.status == "no_specialist"
    assert record.error is None
    assert fakes.delegate_calls == 0


async def test_duplicate_incident_is_not_processed_twice() -> None:
    fakes = Fakes()
    service = IncidentService(classify=fakes.classify, delegate=fakes.delegate)
    await service.start()
    try:
        _, first = service.submit(make_incident("dup"))
        await service.drain()
        record, second = service.submit(make_incident("dup"))
        await service.drain()
    finally:
        await service.stop()
    assert (first, second) == (True, False)
    assert record.status == "awaiting_approval"
    assert fakes.classify_calls == 1


async def test_incident_timeout_marks_failed() -> None:
    async def slow(incident: Incident):  # type: ignore[no-untyped-def]
        await asyncio.sleep(5)

    service = IncidentService(classify=slow, delegate=Fakes().delegate, incident_timeout_s=0.05)
    record = await run_one(service, make_incident())
    assert record.status == "failed" and "limit" in (record.error or "")


async def test_delegation_error_marks_failed() -> None:
    async def boom(incident: Incident, category: str = "cpu_memory"):  # type: ignore[no-untyped-def]
        raise SpecialistError("specialist at http://cpu-memory:8080 timed out")

    record = await run_one(IncidentService(classify=Fakes().classify, delegate=boom),
                           make_incident())
    assert record.status == "failed" and "timed out" in (record.error or "")


async def test_full_queue_is_refused() -> None:
    fakes = Fakes()
    service = IncidentService(classify=fakes.classify, delegate=fakes.delegate, queue_size=1)
    service.submit(make_incident("a"))  # workers not started, so the queue stays full
    with pytest.raises(QueueFull):
        service.submit(make_incident("b"))


def test_store_evicts_oldest() -> None:
    store = IncidentStore(capacity=500)
    for i in range(501):
        store.put(IncidentRecord(incident=make_incident(f"i{i}")))
    assert len(store) == 500
    assert store.get("i0") is None and store.get("i500") is not None


# --- agent with a scripted model --------------------------------------------


def specialist_transport(calls: list[int], *, status: int = 200,
                         delay: float = 0.0) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if delay:
            raise httpx.ReadTimeout("slow", request=request)
        body = investigation().model_dump(mode="json") if status == 200 else {"detail": "x"}
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def orchestrator(model: ScriptedModel, calls: list[int], **transport_args: object) -> Orchestrator:
    client = SpecialistClient("http://cpu-memory:8080", timeout_s=1,
                              transport=specialist_transport(calls, **transport_args))
    return Orchestrator(model=model, settings=SETTINGS, prices=PriceTable({}),
                        specialists={"cpu_memory": client})


async def test_classify_uses_structured_output() -> None:
    model = ScriptedModel([tool_call("Classification", category="cpu_memory",
                                     rationale="The container was OOMKilled.")], model_id=MODEL)
    classification, usage = await orchestrator(model, []).classify(make_incident())
    assert classification.category == "cpu_memory"
    assert usage.input_tokens == 100


async def test_delegation_through_the_tool_calls_the_specialist_once() -> None:
    calls: list[int] = []
    model = ScriptedModel([tool_call("investigate_cpu_memory"), text("Raise the limit.")],
                          model_id=MODEL)
    inv, _, mode = await orchestrator(model, calls).delegate(make_incident(), "cpu_memory")
    assert mode == "tool" and len(calls) == 1
    assert inv.proposal.proposed == "128Mi"


async def test_skipped_tool_is_forced() -> None:
    calls: list[int] = []
    model = ScriptedModel([text("I think it is fine.")], model_id=MODEL)
    inv, _, mode = await orchestrator(model, calls).delegate(make_incident(), "cpu_memory")
    assert mode == "forced" and len(calls) == 1
    assert inv.proposal.action == "adjust_resources"


async def test_specialist_failure_inside_the_tool_is_not_retried() -> None:
    calls: list[int] = []
    model = ScriptedModel([tool_call("investigate_cpu_memory"), text("It failed.")],
                          model_id=MODEL)
    with pytest.raises(SpecialistError):
        await orchestrator(model, calls, delay=1.0).delegate(make_incident(), "cpu_memory")
    assert len(calls) == 1


async def test_specialist_error_status_is_reported() -> None:
    calls: list[int] = []
    model = ScriptedModel([text("skip")], model_id=MODEL)
    with pytest.raises(SpecialistError, match="503"):
        await orchestrator(model, calls, status=503).delegate(make_incident(), "cpu_memory")


# --- HTTP API ---------------------------------------------------------------


def test_api_accepts_reports_and_deduplicates() -> None:
    fakes = Fakes()
    service = IncidentService(classify=fakes.classify, delegate=fakes.delegate)
    with TestClient(create(service)) as client:
        body = make_incident("api-1").model_dump(mode="json")
        first = client.post("/incidents", json=body)
        assert first.status_code == 202 and first.json()["incident_id"] == "api-1"
        for _ in range(50):
            if client.get("/incidents/api-1").json()["status"] == "awaiting_approval":
                break
        again = client.post("/incidents", json=body)
        assert again.status_code == 200
        assert client.get("/incidents/missing").status_code == 404
        assert client.get("/readyz").status_code == 200
    assert fakes.classify_calls == 1


def test_api_rejects_invalid_incident_without_model_calls() -> None:
    fakes = Fakes()
    with TestClient(create(IncidentService(classify=fakes.classify,
                                           delegate=fakes.delegate))) as client:
        response = client.post("/incidents", json={"incident_id": "x", "namespace": "Bad!"})
    assert response.status_code == 422
    assert fakes.classify_calls == 0


def test_api_returns_503_when_queue_is_full() -> None:
    fakes = Fakes()
    service = IncidentService(classify=fakes.classify, delegate=fakes.delegate, queue_size=1)
    app = create(service)
    client = TestClient(app)  # no lifespan: workers never start, the queue fills
    assert client.post("/incidents", json=make_incident("q1").model_dump(mode="json")
                       ).status_code == 202
    assert client.post("/incidents", json=make_incident("q2").model_dump(mode="json")
                       ).status_code == 503
