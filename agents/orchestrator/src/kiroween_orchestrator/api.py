"""Orchestrator HTTP API (REQ-ORC-01 to ORC-05, ORC-12)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from kiroween_shared.bedrock import make_model
from kiroween_shared.config import Settings, env_int
from kiroween_shared.metrics import PriceTable, start_metrics_server
from kiroween_shared.models import Incident, IncidentRecord
from kiroween_shared.server import configure_logging, create_app, serve

from .agent import Orchestrator
from .approval import Approver, GitHubClient
from .memory import MemoryClient
from .service import DecisionConflict, IncidentService, QueueFull
from .specialists import SpecialistClient
from .store import IncidentStore


def create(service: IncidentService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = create_app("orchestrator", ready=lambda: service.running, lifespan=lifespan)

    @app.post("/incidents", status_code=202)
    async def post_incident(incident: Incident, response: Response) -> dict[str, str]:
        try:
            record, created = service.submit(incident)
        except QueueFull as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not created:
            response.status_code = 200
        return {"incident_id": record.incident.incident_id, "status": record.status}

    @app.get("/incidents/{incident_id}", response_model=IncidentRecord)
    async def get_incident(incident_id: str) -> IncidentRecord:
        record = service.store.get(incident_id)
        if record is None:
            raise HTTPException(status_code=404, detail="incident not found")
        return record

    async def decide(incident_id: str, decision: Literal["approved", "rejected"],
                     body: DecisionRequest, request: Request) -> IncidentRecord:
        check_same_origin(request)
        try:
            return await service.decide(incident_id, decision, body.by, body.comment)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="incident not found") from exc
        except DecisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/incidents/{incident_id}/approve", response_model=IncidentRecord)
    async def approve(incident_id: str, body: DecisionRequest,
                      request: Request) -> IncidentRecord:
        return await decide(incident_id, "approved", body, request)

    @app.post("/incidents/{incident_id}/reject", response_model=IncidentRecord)
    async def reject(incident_id: str, body: DecisionRequest,
                     request: Request) -> IncidentRecord:
        return await decide(incident_id, "rejected", body, request)

    return app


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    by: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9 ._@-]+$")
    comment: str = Field(default="", max_length=500)


def check_same_origin(request: Request) -> None:
    """Only JSON from this host (REQ-APR-02).

    A page on another site can make the operator's browser send a "simple" request (form
    or text/plain) through the port-forward. Requiring application/json forces a CORS
    preflight, which this app never answers; the Origin check refuses the rest.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="send application/json")
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(status_code=403, detail="cross-origin decision refused")


SPECIALIST_URL_ENV: dict[str, str] = {
    "cpu_memory": "CPU_MEMORY_URL",
    "network": "NETWORK_URL",
    "deploy_rollout": "DEPLOY_ROLLOUT_URL",
    "storage": "STORAGE_URL",
}


def specialists_from_env(env: Mapping[str, str], timeout_s: float) -> dict[str, SpecialistClient]:
    """One client per category whose URL is set; others end as no_specialist (REQ-ORX-01)."""
    return {
        category: SpecialistClient(env[var], timeout_s=timeout_s)
        for category, var in SPECIALIST_URL_ENV.items()
        if env.get(var)
    }


def main() -> None:
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    env = os.environ
    settings = Settings.from_env("orchestrator")
    prices = PriceTable.from_json(settings.prices_json)
    specialists = specialists_from_env(env, env_int(env, "DELEGATION_TIMEOUT_SECONDS", 120))
    orchestrator = Orchestrator(
        model=make_model(settings), settings=settings, prices=prices, specialists=specialists
    )
    memory_url = env.get("INCIDENT_MEMORY_URL", "")
    memory = MemoryClient(memory_url) if memory_url else None
    # Optional Secret orchestrator-github/token (REQ-APR-05). Without it: dry-run PRs.
    token = env.get("GITHUB_TOKEN", "")
    github = GitHubClient(token, env.get("GITHUB_REPOSITORY", ""),
                          base=env.get("GITHUB_BASE_BRANCH", "main")) if token else None
    service = IncidentService(
        classify=orchestrator.classify,
        delegate=orchestrator.delegate,
        store=IncidentStore(env_int(env, "STORE_CAPACITY", 500)),
        queue_size=env_int(env, "QUEUE_SIZE", 20),
        workers=env_int(env, "WORKERS", 2),
        incident_timeout_s=env_int(env, "INCIDENT_TIMEOUT_SECONDS", 300),
        specialists=frozenset(specialists),
        correlation_window_s=env_int(env, "CORRELATION_WINDOW_SECONDS", 300),
        similar=memory.similar if memory else None,
        approver=Approver(github),
    )
    start_metrics_server(settings.metrics_port)
    serve(create(service), settings.api_port)
