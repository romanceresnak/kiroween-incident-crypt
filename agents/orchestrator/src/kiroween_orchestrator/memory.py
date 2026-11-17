"""Client for incident-memory (memory-and-ui REQ-MEM-06).

Similar past incidents are useful context, never a dependency: every failure returns an
empty list and the incident carries on.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import TypeAdapter

from kiroween_shared.models import Incident, SimilarIncident

logger = logging.getLogger(__name__)

_RESULTS = TypeAdapter(list[SimilarIncident])


class MemoryClient:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._transport = transport

    async def similar(self, incident: Incident) -> list[SimilarIncident]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s,
                                         transport=self._transport) as client:
                response = await client.post(f"{self.base_url}/similar",
                                             json=incident.model_dump(mode="json"))
                response.raise_for_status()
                return _RESULTS.validate_python(response.json())[:5]
        except Exception as exc:
            logger.warning("incident-memory unavailable (%s); continuing without similar "
                           "incidents", type(exc).__name__)
            return []
