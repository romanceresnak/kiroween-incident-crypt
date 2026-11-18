"""The lab-runner's HTTP API and entrypoint (scenario-lab REQ-RN-04).

Reachable only from the orchestrator (NetworkPolicy). It accepts a scenario id and nothing else.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from kiroween_shared.config import env_int
from kiroween_shared.server import configure_logging, create_app, serve

from .limits import RunLimits
from .runner import Busy, LabRunner, RateLimited, RunnerError

logger = logging.getLogger(__name__)

TICK_S = 5.0


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")


def create(runner: LabRunner) -> FastAPI:
    app = create_app("lab-runner", ready=lambda: runner.started)

    @app.exception_handler(RunnerError)
    async def runner_error(request: Request, exc: RunnerError) -> JSONResponse:
        body: dict[str, Any] = {"detail": str(exc)}
        headers: dict[str, str] = {}
        if isinstance(exc, Busy) and exc.scenario:
            body["scenario"] = exc.scenario
        if isinstance(exc, RateLimited):
            body["retry_after"] = exc.retry_after_s
            headers["Retry-After"] = str(exc.retry_after_s)
        return JSONResponse(body, status_code=exc.status, headers=headers)

    @app.get("/status")
    def status() -> dict[str, Any]:
        return runner.status()

    @app.post("/run", status_code=202)
    def run(body: RunRequest) -> dict[str, Any]:
        return runner.run(body.scenario)

    @app.post("/reset")
    def reset() -> dict[str, Any]:
        return runner.reset()

    return app


def main() -> None:
    from .kube import KubernetesWriter

    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    env = os.environ
    limits = RunLimits(max_per_hour=env_int(env, "LAB_MAX_RUNS_PER_HOUR", 10),
                       cleanup_s=env_int(env, "LAB_CLEANUP_SECONDS", 900))
    runner = LabRunner(KubernetesWriter.in_cluster(), limits)

    def start_and_tick() -> None:
        runner.start()
        while True:
            try:
                runner.tick()
            except Exception:
                logger.exception("the expiry check failed")
            time.sleep(TICK_S)

    threading.Thread(target=start_and_tick, name="lab-tick", daemon=True).start()
    serve(create(runner), env_int(env, "API_PORT", 8080))
