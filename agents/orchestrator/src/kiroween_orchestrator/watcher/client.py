"""Deliver incidents to the orchestrator with bounded retry (REQ-WAT-06)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx

from kiroween_shared.models import Incident

logger = logging.getLogger(__name__)


class OrchestratorClient:
    def __init__(
        self,
        base_url: str,
        *,
        attempts: int = 3,
        backoff_s: float = 1.0,
        timeout_s: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        token: str = "",
    ) -> None:
        # The orchestrator requires a login; the watcher has a token instead (scenario-lab
        # REQ-AU-09). It is sent only to POST /incidents and never logged.
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s, transport=transport,
                                    headers=headers)
        self._attempts = attempts
        self._backoff = backoff_s
        self._sleep = sleep

    def submit(self, incident: Incident) -> bool:
        """Post one incident. Returns False if it was dropped; never queues without bound."""
        # A watcher that is newer than the orchestrator must not send a field the old one rejects:
        # `resources` goes only when it was read.
        body = incident.model_dump(
            mode="json", exclude=None if incident.resources is not None else {"resources"})
        for attempt in range(self._attempts):
            try:
                response = self._client.post("/incidents", json=body)
            except httpx.HTTPError as exc:
                logger.warning("orchestrator unreachable (attempt %d): %s", attempt + 1, exc)
            else:
                if response.status_code in (200, 202):
                    return True
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    logger.error("orchestrator rejected incident %s: %s %s",
                                 incident.incident_id, response.status_code, response.text[:200])
                    return False
                logger.warning("orchestrator returned %s (attempt %d)",
                               response.status_code, attempt + 1)
            if attempt + 1 < self._attempts:
                self._sleep(self._backoff * 2**attempt)
        logger.error("dropping incident %s after %d attempts", incident.incident_id,
                     self._attempts)
        return False
