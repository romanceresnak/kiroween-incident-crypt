"""specialists TASK-07: orchestrator delegation to every specialist."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from kiroween_orchestrator.agent import SIGNAL_CATEGORIES, Orchestrator, apply_guards
from kiroween_orchestrator.api import specialists_from_env
from kiroween_orchestrator.service import IncidentService
from kiroween_orchestrator.specialists import SpecialistClient
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
ALL = frozenset({"cpu_memory", "network", "deploy_rollout", "storage"})


def incident(reason: str = "Unhealthy", source: str = "watcher", iid: str = "x1") -> Incident:
    return Incident.model_validate({
        "incident_id": iid, "source": source, "reason": reason, "namespace": "shop",
        "workload": {"kind": "Deployment", "name": "web"}, "pod": "web-1", "container": "web",
        "observed_at": datetime(2026, 11, 17, tzinfo=UTC)})


def set_field_investigation() -> Investigation:
    return Investigation(
        diagnosis=Diagnosis(root_cause="policy blocks client", confidence="high"),
        proposal=Proposal(action="set_field",
                          target=Target(namespace="shop", kind="NetworkPolicy", name="p"),
                          field="spec.ingress[0].from[0].podSelector.matchLabels.app",
                          current="a", proposed="b"),
        usage=Usage(input_tokens=500, output_tokens=80))


class Fakes:
    def __init__(self, category: str, confidence: str = "high") -> None:
        self.category, self.confidence = category, confidence
        self.delegated: list[str] = []

    async def classify(self, inc: Incident):  # type: ignore[no-untyped-def]
        return Classification(category=self.category, rationale="r",
                              confidence=self.confidence), Usage(input_tokens=10)

    async def delegate(self, inc: Incident, category: str):  # type: ignore[no-untyped-def]
        self.delegated.append(category)
        return set_field_investigation(), Usage(input_tokens=20), "tool"


async def process(service: IncidentService, inc: Incident) -> IncidentRecord:
    await service.start()
    try:
        service.submit(inc)
        await asyncio.wait_for(service.drain(), timeout=5)
    finally:
        await service.stop()
    return service.store.get(inc.incident_id)  # type: ignore[return-value]


# --- service routing ------------------------------------------------------------


@pytest.mark.parametrize("category,agent", [("network", "network"),
                                            ("deploy_rollout", "deploy-rollout"),
                                            ("storage", "storage")])
async def test_each_category_routes_to_its_specialist(category: str, agent: str) -> None:
    fakes = Fakes(category)
    record = await process(IncidentService(classify=fakes.classify, delegate=fakes.delegate,
                                           specialists=ALL), incident())
    assert fakes.delegated == [category]
    assert record.status == "awaiting_approval"  # set_field waits for a human
    assert record.usage_by_agent[agent].input_tokens == 500


async def test_category_without_a_configured_specialist() -> None:
    fakes = Fakes("storage")
    record = await process(IncidentService(classify=fakes.classify, delegate=fakes.delegate,
                                           specialists=frozenset({"cpu_memory"})), incident())
    assert record.status == "no_specialist" and fakes.delegated == []


def test_specialists_from_env_only_includes_configured_urls() -> None:
    clients = specialists_from_env({"CPU_MEMORY_URL": "http://cpu-memory:8080",
                                    "NETWORK_URL": "http://network:8080", "STORAGE_URL": ""}, 5)
    assert set(clients) == {"cpu_memory", "network"}


# --- guards -------------------------------------------------------------------


def test_low_confidence_becomes_unknown() -> None:
    c = apply_guards(incident(), Classification(category="network", rationale="maybe",
                                                confidence="low"))
    assert (c.category, c.confidence) == ("unknown", "low")
    assert "low confidence" in c.rationale


@pytest.mark.parametrize("reason,wrong", [("ImagePullBackOff", "network"),
                                          ("OOMKilled", "storage"),
                                          ("ProvisioningFailed", "deploy_rollout"),
                                          ("Unhealthy", "storage")])
def test_category_not_allowed_for_the_signal_becomes_unknown(reason: str, wrong: str) -> None:
    c = apply_guards(incident(reason), Classification(category=wrong, rationale="r",
                                                      confidence="high"))
    assert c.category == "unknown"
    assert f"not an expected category for signal {reason}" in c.rationale


@pytest.mark.parametrize("reason,allowed", sorted(SIGNAL_CATEGORIES.items()))
def test_allowed_categories_pass(reason: str, allowed: frozenset[str]) -> None:
    for category in allowed:
        c = Classification(category=category, rationale="r", confidence="high")
        assert apply_guards(incident(reason), c).category == category


def test_api_submitted_incidents_are_not_signal_guarded() -> None:
    c = Classification(category="network", rationale="r", confidence="high")
    assert apply_guards(incident("ImagePullBackOff", source="api"), c).category == "network"


async def test_guarded_incident_is_not_delegated() -> None:
    fakes = Fakes("network", confidence="low")

    async def classify(inc: Incident):  # type: ignore[no-untyped-def]
        c, usage = await fakes.classify(inc)
        return apply_guards(inc, c), usage

    service = IncidentService(classify=classify, delegate=fakes.delegate, specialists=ALL)
    record = await process(service, incident())
    assert record.status == "no_specialist" and fakes.delegated == []


# --- the delegation agent --------------------------------------------------------------


def transport(calls: list[str]) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=set_field_investigation().model_dump(mode="json"))
    return httpx.MockTransport(handler)


def orchestrator(model: ScriptedModel, calls: list[str]) -> Orchestrator:
    clients = {c: SpecialistClient(f"http://{c.replace('_', '-')}:8080", timeout_s=1,
                                   transport=transport(calls)) for c in ALL}
    return Orchestrator(model=model, settings=Settings(agent_name="orchestrator",
                                                       model_id=MODEL),
                        prices=PriceTable({}), specialists=clients)


@pytest.mark.parametrize("category", ["network", "deploy_rollout", "storage"])
async def test_delegation_agent_sees_only_its_category_tool(category: str) -> None:
    calls: list[str] = []
    model = ScriptedModel([tool_call(f"investigate_{category}"), text("done")], model_id=MODEL)
    _, _, mode = await orchestrator(model, calls).delegate(incident(), category)
    assert model.tool_specs_seen[0] == [f"investigate_{category}"]
    assert mode == "tool"
    assert calls == [f"http://{category.replace('_', '-')}:8080/investigate"]


async def test_a_tool_for_another_category_is_not_available() -> None:
    calls: list[str] = []
    # The model asks for a tool it was not given; the call fails and the orchestrator
    # still routes to the right specialist itself.
    model = ScriptedModel([tool_call("investigate_storage"), text("done")], model_id=MODEL)
    _, _, mode = await orchestrator(model, calls).delegate(incident(), "network")
    assert mode == "forced"
    assert calls == ["http://network:8080/investigate"]


async def test_classifier_output_is_guarded() -> None:
    model = ScriptedModel([tool_call("Classification", category="storage", rationale="r",
                                     confidence="high")], model_id=MODEL)
    classification, _ = await orchestrator(model, []).classify(incident("ImagePullBackOff"))
    assert classification.category == "unknown"
