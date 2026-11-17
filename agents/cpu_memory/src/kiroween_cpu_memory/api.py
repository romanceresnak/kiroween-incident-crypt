"""cpu-memory HTTP API: POST /investigate (REQ-CPU-01)."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from decimal import Decimal

from fastapi import FastAPI, HTTPException

from kiroween_shared.bedrock import CallBudgetExceeded, ModelUnavailable, make_model
from kiroween_shared.config import Settings
from kiroween_shared.context import incident_context
from kiroween_shared.k8s.client import ReadOnlyKube
from kiroween_shared.metrics import PriceTable, start_metrics_server
from kiroween_shared.models import Incident, Investigation
from kiroween_shared.server import configure_logging, create_app, serve

from .agent import investigate

logger = logging.getLogger(__name__)

Investigator = Callable[[Incident], Awaitable[Investigation]]


def create(investigator: Investigator) -> FastAPI:
    app = create_app("cpu-memory", ready=lambda: True)

    @app.post("/investigate", response_model=Investigation)
    async def post_investigate(incident: Incident) -> Investigation:
        with incident_context(incident.incident_id):
            try:
                return await investigator(incident)
            except ModelUnavailable as exc:
                logger.error("%s", exc)
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except CallBudgetExceeded as exc:
                logger.error("%s", exc)
                raise HTTPException(status_code=500, detail=str(exc)) from exc

    return app


def main() -> None:
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    settings = Settings.from_env("cpu-memory")
    prices = PriceTable.from_json(settings.prices_json)
    max_factor = Decimal(os.environ.get("MAX_INCREASE_FACTOR", "4"))
    kube = ReadOnlyKube()
    model = make_model(settings)

    async def investigator(incident: Incident) -> Investigation:
        return await investigate(
            incident, kube=kube, model=model, settings=settings, prices=prices,
            max_factor=max_factor,
        )

    start_metrics_server(settings.metrics_port)
    serve(create(investigator), settings.api_port)
