"""HTTP API of incident-memory: ``POST /similar`` (REQ-MEM-03)."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException

from kiroween_shared.context import incident_context
from kiroween_shared.models import Incident, SimilarIncident
from kiroween_shared.server import create_app

from .memory import Memory, RetrieveFailed


def create(memory: Memory) -> FastAPI:
    app = create_app("incident-memory", ready=lambda: True)

    @app.post("/similar", response_model=list[SimilarIncident])
    async def post_similar(incident: Incident) -> list[SimilarIncident]:
        with incident_context(incident.incident_id):
            try:
                return await asyncio.to_thread(memory.similar, incident)
            except RetrieveFailed as exc:
                raise HTTPException(status_code=503,
                                    detail=f"knowledge base retrieve failed ({exc})") from exc

    return app
