"""HTTP app factory, health endpoints and JSON logging."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .context import current_incident_id


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the current incident id (REQ-ORC-14)."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "incident_id": current_incident_id(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def create_app(
    title: str,
    ready: Callable[[], bool],
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[Any]] | None = None,
) -> FastAPI:
    """FastAPI app with liveness and readiness probes.

    ``/readyz`` never calls Bedrock, so probes cost nothing. API docs are disabled.
    """
    app = FastAPI(title=title, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        ok = ready()
        return JSONResponse({"ready": ok}, status_code=200 if ok else 503)

    return app


def serve(app: FastAPI, port: int) -> None:
    # Binds all interfaces because it runs in a pod; the network policies limit who
    # can connect.
    uvicorn.run(app, host="0.0.0.0", port=port, log_config=None)  # noqa: S104
