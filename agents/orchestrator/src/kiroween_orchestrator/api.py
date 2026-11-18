"""Orchestrator HTTP API (REQ-ORC-01 to ORC-05, ORC-12)."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from kiroween_shared.bedrock import make_model
from kiroween_shared.config import Settings, env_int
from kiroween_shared.metrics import PriceTable, start_metrics_server
from kiroween_shared.models import Incident, IncidentRecord
from kiroween_shared.server import configure_logging, create_app, serve

from . import auth as auth_module
from .agent import Orchestrator
from .approval import Approver, GitHubClient
from .auth import Auth, AuthConfig, is_lab_namespace
from .lab import routes as lab_routes
from .lab.client import LabClient
from .learning import build_learner
from .memory import MemoryClient
from .service import DecisionConflict, IncidentService, QueueFull
from .specialists import SpecialistClient
from .store import IncidentStore

logger = logging.getLogger(__name__)


def create(service: IncidentService, auth: Auth | None = None,
           lab: LabClient | None = None) -> FastAPI:
    """The API and the UI. With ``auth`` every route except the health probes and the login
    needs a session (or, for POST /incidents, the watcher's token). Without it nothing does,
    which is how the tests and a local run use it; main() always passes one. ``lab`` is the
    client of the lab-runner; without it the Lab answers 503."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = create_app("orchestrator", ready=lambda: service.running, lifespan=lifespan)

    def lab_only(request: Request) -> bool:
        """A signed-in judge may act on lab incidents only (REQ-AU-13). The watcher's token,
        and an app without login (tests, a local run), are not limited."""
        return (auth is not None and auth.config.lab_only
                and getattr(request.state, "auth_kind", "open") == "session")

    @app.post("/incidents", status_code=202)
    async def post_incident(incident: Incident, request: Request,
                            response: Response) -> dict[str, str]:
        if lab_only(request) and not is_lab_namespace(incident.namespace):
            raise HTTPException(status_code=403,
                                detail="this account can submit lab incidents only")
        try:
            record, created = service.submit(incident)
        except QueueFull as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not created:
            response.status_code = 200
        return {"incident_id": record.incident.incident_id, "status": record.status}

    @app.get("/incidents", response_model=list[IncidentRecord])
    async def list_incidents(limit: int = Query(default=50, ge=1, le=200)) -> list[IncidentRecord]:
        """Most recent incidents first, for the UI feed (REQ-UI-01)."""
        records = sorted(service.store.records(), key=lambda r: r.created_at, reverse=True)
        return records[:limit]

    @app.get("/incidents/{incident_id}", response_model=IncidentRecord)
    async def get_incident(incident_id: str) -> IncidentRecord:
        record = service.store.get(incident_id)
        if record is None:
            raise HTTPException(status_code=404, detail="incident not found")
        return record

    async def decide(incident_id: str, decision: Literal["approved", "rejected"],
                     body: DecisionRequest, request: Request) -> IncidentRecord:
        check_same_origin(request, auth)
        if lab_only(request):
            record = service.store.get(incident_id)
            if record is not None and not is_lab_namespace(record.incident.namespace):
                # A real incident's pull request would go to the main branch.
                raise HTTPException(status_code=403,
                                    detail="this account can decide lab incidents only")
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

    # Installed before ui_headers, so ui_headers wraps it and its responses get the headers too.
    if auth is not None:
        auth_module.install(app, auth)
    lab_routes.install(app, service, lab, auth)

    # The UI: static files in this package, same origin as the API (REQ-UI-01, UI-04).
    @app.middleware("http")
    async def ui_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        path = request.url.path
        if (path == "/ui" or path.startswith("/ui/") or path == "/login"
                or path.startswith("/login-assets/")):
            response.headers["Content-Security-Policy"] = UI_CSP
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")

    return app


UI_DIR = Path(__file__).parent / "ui"
UI_CSP = ("default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; "
          "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
          "frame-ancestors 'none'")


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    by: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9 ._@-]+$")
    comment: str = Field(default="", max_length=500)


# The implementation lives in auth.py (scenario-lab REQ-AU-08); the name stays importable.
check_same_origin = auth_module.check_same_origin


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
        # Off unless LEARNING_BUCKET, KNOWLEDGE_BASE_ID and KNOWLEDGE_BASE_DATA_SOURCE_ID are
        # all set (polish REQ-LRN-07).
        learner=build_learner(env),
    )
    auth = Auth(AuthConfig.from_env(env))
    if auth.config.required and not auth.config.configured:
        logger.warning("login is required but the Secret orchestrator-auth has no username and "
                       "password: every route except the health probes answers 503")
    if auth.config.required and not auth.config.internal_token:
        logger.warning("no internal token: the watcher cannot submit incidents")
    lab_url = env.get("LAB_RUNNER_URL", "")
    lab = LabClient(lab_url) if lab_url else None
    start_metrics_server(settings.metrics_port)
    serve(create(service, auth, lab), settings.api_port)
