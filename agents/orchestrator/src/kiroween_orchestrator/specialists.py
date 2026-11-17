"""Specialists as tools (REQ-ORC-07, ORC-08).

The orchestrator calls each specialist over cluster-internal HTTP; specialists never call
each other. The tool takes no arguments: it always sends the incident the orchestrator
accepted, so the model cannot change what is forwarded.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from strands import tool

from kiroween_shared.models import Incident, Investigation

logger = logging.getLogger(__name__)


class SpecialistError(RuntimeError):
    pass


class SpecialistClient:
    def __init__(
        self, base_url: str, timeout_s: float, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.base_url = base_url
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s, transport=transport)

    async def investigate(self, incident: Incident) -> Investigation:
        try:
            response = await self._client.post(
                "/investigate", json=incident.model_dump(mode="json")
            )
        except httpx.TimeoutException as exc:
            raise SpecialistError(f"specialist at {self.base_url} timed out") from exc
        except httpx.HTTPError as exc:
            raise SpecialistError(f"specialist at {self.base_url} unreachable: {exc}") from exc
        if response.status_code != 200:
            raise SpecialistError(
                f"specialist returned {response.status_code}: {response.text[:200]}"
            )
        return Investigation.model_validate(response.json())

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass
class DelegationSink:
    """Holds the specialist's real answer so the record never relies on the model's retelling."""

    investigation: Investigation | None = None
    error: Exception | None = None
    calls: int = 0


def make_cpu_memory_tool(client: SpecialistClient, incident: Incident, sink: DelegationSink) -> Any:
    @tool
    async def investigate_cpu_memory() -> str:
        """Ask the cpu-memory specialist to diagnose the incident under review (an OOMKilled
        container or CPU throttling) and propose a fix. Takes no arguments."""
        if sink.investigation is None and sink.error is None:
            sink.calls += 1
            try:
                sink.investigation = await client.investigate(incident)
            except Exception as exc:
                sink.error = exc
                raise
        if sink.investigation is None:
            raise SpecialistError("the specialist call already failed")
        inv = sink.investigation
        return json.dumps({
            "root_cause": inv.diagnosis.root_cause,
            "confidence": inv.diagnosis.confidence,
            "action": inv.proposal.action,
        })

    return investigate_cpu_memory
