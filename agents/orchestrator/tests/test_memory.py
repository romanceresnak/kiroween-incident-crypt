"""memory-and-ui TASK-04: similar incidents in the orchestrator, and its metrics."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from kiroween_orchestrator.memory import MemoryClient
from kiroween_orchestrator.service import IncidentService
from kiroween_shared.metrics import REGISTRY
from kiroween_shared.models import (
    Classification,
    Diagnosis,
    Incident,
    Investigation,
    Proposal,
    SimilarIncident,
    Target,
    Usage,
)
from kiroween_shared.specialist import COMMON_PROMPT

HIT = SimilarIncident(title="PM-2026-031: report-renderer OOMKilled", category="cpu_memory",
                      score=0.74, fix="raise the limit from 256Mi to 512Mi",
                      source="s3://b/postmortems/01.md")


def incident(iid: str = "mem-1", name: str = "oom-demo") -> Incident:
    return Incident.model_validate({
        "incident_id": iid, "source": "watcher", "reason": "OOMKilled", "namespace": "chaos-oom",
        "workload": {"kind": "Deployment", "name": name}, "pod": "oom-demo-1", "container": "app",
        "observed_at": datetime(2026, 11, 17, tzinfo=UTC)})


class Fakes:
    def __init__(self, category: str = "cpu_memory", fail: bool = False) -> None:
        self.category = category
        self.fail = fail
        self.delegated: list[Incident] = []
        self.memory_calls: list[str] = []
        self.overlap = False
        self._in_classify = False

    async def classify(self, i: Incident):  # type: ignore[no-untyped-def]
        self._in_classify = True
        await asyncio.sleep(0.05)
        self._in_classify = False
        return Classification(category=self.category, rationale="r"), Usage(input_tokens=1)

    async def similar(self, i: Incident) -> list[SimilarIncident]:
        self.memory_calls.append(i.incident_id)
        self.overlap = self.overlap or self._in_classify
        if self.fail:
            raise RuntimeError("boom")
        return [HIT]

    async def delegate(self, i: Incident, category: str):  # type: ignore[no-untyped-def]
        self.delegated.append(i)
        inv = Investigation(
            diagnosis=Diagnosis(root_cause="limit too low", confidence="high"),
            proposal=Proposal(action="adjust_resources",
                              target=Target(namespace="chaos-oom", kind="Deployment",
                                            name="oom-demo", container="app"),
                              field="resources.limits.memory", current="64Mi",
                              proposed="128Mi"))
        return inv, Usage(), "tool"


async def process(svc: IncidentService, *incidents: Incident) -> None:
    await svc.start()
    try:
        for i in incidents:
            svc.submit(i)
        await asyncio.wait_for(svc.drain(), timeout=5)
    finally:
        await svc.stop()


def service(f: Fakes, **kw) -> IncidentService:  # type: ignore[no-untyped-def]
    return IncidentService(classify=f.classify, delegate=f.delegate, similar=f.similar,
                           specialists=frozenset({"cpu_memory"}), **kw)


async def test_similar_incidents_are_recorded_and_passed_to_the_specialist() -> None:
    f = Fakes()
    svc = service(f)
    await process(svc, incident())
    record = svc.store.get("mem-1")
    assert [h.title for h in record.similar_incidents] == [HIT.title]
    assert f.delegated[0].similar_incidents == [HIT]
    assert record.incident.similar_incidents == []  # the submitted incident is unchanged


async def test_memory_runs_while_the_incident_is_classified() -> None:
    f = Fakes()
    await process(service(f), incident())
    assert f.overlap


async def test_memory_failure_never_fails_the_incident() -> None:
    f = Fakes(fail=True)
    svc = service(f)
    await process(svc, incident())
    record = svc.store.get("mem-1")
    assert record.status == "awaiting_approval" and record.similar_incidents == []


async def test_no_memory_configured() -> None:
    f = Fakes()
    svc = IncidentService(classify=f.classify, delegate=f.delegate,
                          specialists=frozenset({"cpu_memory"}))
    await process(svc, incident())
    assert svc.store.get("mem-1").similar_incidents == []


async def test_memory_is_queried_for_incidents_without_a_specialist() -> None:
    f = Fakes(category="unknown")
    svc = service(f)
    await process(svc, incident())
    assert svc.store.get("mem-1").status == "no_specialist"
    assert svc.store.get("mem-1").similar_incidents == [HIT]


async def test_correlated_incidents_are_not_queried() -> None:
    f = Fakes()
    await process(service(f), incident("a"), incident("b"))  # same workload -> correlated
    assert f.memory_calls == ["a"]


# --- HTTP client ----------------------------------------------------------------------------


def transport(handler):  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


async def test_client_posts_the_incident_and_parses_hits() -> None:
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(200, json=[HIT.model_dump(mode="json")] * 7)

    hits = await MemoryClient("http://incident-memory:8080/",
                              transport=transport(handler)).similar(incident())
    assert seen == [("POST", "http://incident-memory:8080/similar")]
    assert len(hits) == 5 and hits[0].fix.startswith("raise")


async def test_client_returns_empty_on_errors() -> None:
    async def error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down"})

    async def garbage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "a list"})

    async def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    for handler in (error, garbage, refuse):
        assert await MemoryClient("http://m", transport=transport(handler)).similar(
            incident()) == []


# --- metrics (REQ-MON-05) -------------------------------------------------------------------


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_incident_and_latency_metrics() -> None:
    labels = {"category": "cpu_memory", "status": "awaiting_approval",
              "outcome": "action_required"}
    before = sample("orchestrator_incidents_total", **labels)
    count_before = sample("orchestrator_investigation_seconds_count", category="cpu_memory")
    await process(service(Fakes()), incident("met-1", "m1"))
    assert sample("orchestrator_incidents_total", **labels) == before + 1
    assert sample("orchestrator_investigation_seconds_count",
                  category="cpu_memory") == count_before + 1


async def test_correlated_and_failed_are_counted() -> None:
    # Both are submitted before the primary is classified, so the category is not known yet.
    correlated = sample("orchestrator_incidents_total", category="none",
                        status="correlated", outcome="none")
    await process(service(Fakes()), incident("c1", "w1"), incident("c2", "w1"))
    assert sample("orchestrator_incidents_total", category="none", status="correlated",
                  outcome="none") == correlated + 1

    async def boom(i: Incident, category: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("specialist down")

    f = Fakes()
    failed = sample("orchestrator_incidents_total", category="cpu_memory", status="failed",
                    outcome="none")
    svc = IncidentService(classify=f.classify, delegate=boom, similar=f.similar,
                          specialists=frozenset({"cpu_memory"}))
    await process(svc, incident("f1", "w2"))
    assert sample("orchestrator_incidents_total", category="cpu_memory", status="failed",
                  outcome="none") == failed + 1


def test_common_prompt_treats_similar_incidents_as_hints() -> None:
    text = " ".join(COMMON_PROMPT.split())
    assert "similar_incidents" in text and "Never cite them as evidence" in text
