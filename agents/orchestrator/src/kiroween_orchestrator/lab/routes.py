"""The Lab endpoints of the orchestrator (scenario-lab REQ-AP-01 to AP-05).

All of them sit behind the login (the middleware in auth.py); the two POSTs also check the
origin. The orchestrator only asks the runner: it never touches the cluster itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from kiroween_shared.models import IncidentRecord

from ..auth import Auth, check_same_origin
from ..service import IncidentService
from . import catalog
from .client import LabClient, LabError, LabUnavailable
from .scenarios import BY_ID

# A little slack for clocks that differ by a moment.
SLACK = timedelta(seconds=2)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")


def primary_incident(records: list[IncidentRecord], namespace: str,
                     started_at: str) -> dict[str, Any] | None:
    """The incident this run produced: the oldest in its namespace since it started that is not
    attached to another. The others are counted (REQ-AP-03)."""
    try:
        since = datetime.fromisoformat(started_at) - SLACK
    except ValueError:
        return None
    mine = [r for r in records if r.incident.namespace == namespace and r.created_at >= since]
    primaries = sorted((r for r in mine if r.related_to is None), key=lambda r: r.created_at)
    if not primaries:
        return None
    found = primaries[0]
    category = found.classification.category if found.classification else None
    return {
        "incident_id": found.incident.incident_id, "status": found.status,
        "category": category, "reason": found.incident.reason,
        "workload": f"{found.incident.workload.kind}/{found.incident.workload.name}",
        "correlated": len(mine) - len(primaries),
    }


def install(app: FastAPI, service: IncidentService, client: LabClient | None,
            auth: Auth | None) -> None:
    def runner() -> LabClient:
        if client is None:
            raise HTTPException(status_code=503, detail="the lab is not configured")
        return client

    async def call(coro: Any) -> dict[str, Any]:
        try:
            return await coro
        except LabUnavailable as exc:
            raise HTTPException(status_code=503,
                                detail="the lab runner is not available right now") from exc
        except LabError as exc:
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
            raise HTTPException(status_code=exc.status, detail=exc.detail,
                                headers=headers) from exc

    @app.get("/lab/scenarios")
    async def scenarios() -> list[dict[str, Any]]:
        return catalog.public()

    @app.get("/lab/status")
    async def status() -> dict[str, Any]:
        data = await call(runner().status())
        incident = None
        if data.get("state") != "idle" and data.get("namespace") and data.get("started_at"):
            incident = primary_incident(service.store.records(), data["namespace"],
                                        data["started_at"])
        return {**data, "incident": incident}

    @app.post("/lab/run", status_code=202)
    async def run(body: RunRequest, request: Request) -> dict[str, Any]:
        check_same_origin(request, auth)
        if body.scenario not in BY_ID:
            raise HTTPException(status_code=404, detail="unknown scenario")
        return await call(runner().run(body.scenario))

    @app.post("/lab/reset")
    async def reset(request: Request) -> dict[str, Any]:
        check_same_origin(request, auth)
        return await call(runner().reset())
