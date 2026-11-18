"""polish TASK-03: the orchestrator's incident-memory client sends the category."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from kiroween_orchestrator.memory import MemoryClient
from kiroween_shared.models import Incident, SimilarIncident

INCIDENT = Incident.model_validate({
    "incident_id": "c-1", "source": "watcher", "reason": "Unhealthy", "namespace": "chaos-netpol",
    "workload": {"kind": "Deployment", "name": "netpol-client"},
    "observed_at": datetime(2026, 11, 17, tzinfo=UTC)})
HIT = SimilarIncident(title="PM-2026-058", category="network", score=0.71, fix="relabel")


def client_and_log() -> tuple[MemoryClient, list[httpx.Request]]:
    log: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        return httpx.Response(200, json=[HIT.model_dump(mode="json")])

    return MemoryClient("http://incident-memory:8080",
                        transport=httpx.MockTransport(handler)), log


@pytest.mark.parametrize("category", ["cpu_memory", "network", "deploy_rollout", "storage"])
async def test_the_category_is_a_query_parameter(category: str) -> None:
    client, log = client_and_log()
    hits = await client.similar(INCIDENT, category)
    assert [h.title for h in hits] == ["PM-2026-058"]
    assert str(log[0].url) == f"http://incident-memory:8080/similar?category={category}"
    assert log[0].method == "POST"


@pytest.mark.parametrize("category", [None, "unknown", ""])
async def test_no_parameter_without_a_usable_category(category: str | None) -> None:
    client, log = client_and_log()
    await client.similar(INCIDENT, category)
    assert str(log[0].url) == "http://incident-memory:8080/similar"


async def test_the_body_is_still_just_the_incident() -> None:
    client, log = client_and_log()
    await client.similar(INCIDENT, "network")
    import json

    assert json.loads(log[0].content)["incident_id"] == "c-1"
    assert "category" not in json.loads(log[0].content)   # an old pod must still accept it
