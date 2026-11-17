"""Queue, workers and the incident lifecycle (REQ-ORC-03, ORC-05, ORC-09 to ORC-12)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from kiroween_shared.context import incident_context
from kiroween_shared.models import (
    Classification,
    Incident,
    IncidentRecord,
    Investigation,
    Usage,
)

from .specialists import SPECIALIST_AGENTS
from .store import IncidentStore

logger = logging.getLogger(__name__)

Classify = Callable[[Incident], Awaitable[tuple[Classification, Usage]]]
Delegate = Callable[
    [Incident, str], Awaitable[tuple[Investigation, Usage, Literal["tool", "forced"]]]
]

# A proposal of one of these actions waits for a human (core REQ-ORC-10).
CHANGE_ACTIONS = frozenset({"adjust_resources", "set_field"})


class QueueFull(RuntimeError):
    pass


class IncidentService:
    def __init__(
        self,
        *,
        classify: Classify,
        delegate: Delegate,
        store: IncidentStore | None = None,
        queue_size: int = 20,
        workers: int = 2,
        incident_timeout_s: float = 300.0,
        specialists: frozenset[str] = frozenset({"cpu_memory"}),
    ) -> None:
        # Categories with a configured specialist. Everything else ends as no_specialist.
        self.specialists = specialists
        self._classify = classify
        self._delegate = delegate
        self.store = store or IncidentStore()
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_size)
        self._workers = workers
        self._timeout = incident_timeout_s
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def running(self) -> bool:
        return bool(self._tasks) and not any(t.done() for t in self._tasks)

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._worker()) for _ in range(self._workers)]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    async def drain(self) -> None:
        await self._queue.join()

    def submit(self, incident: Incident) -> tuple[IncidentRecord, bool]:
        """Accept an incident. Returns the record and whether it is new (REQ-ORC-05)."""
        existing = self.store.get(incident.incident_id)
        if existing is not None:
            return existing, False
        if self._queue.full():
            raise QueueFull("incident queue is full")
        record = IncidentRecord(incident=incident)
        self.store.put(record)
        self._queue.put_nowait(incident.incident_id)
        return record, True

    async def _worker(self) -> None:
        while True:
            incident_id = await self._queue.get()
            try:
                await self._process(incident_id)
            except Exception:
                logger.exception("unexpected error processing %s", incident_id)
            finally:
                self._queue.task_done()

    async def _process(self, incident_id: str) -> None:
        record = self.store.get(incident_id)
        if record is None:  # evicted while queued
            return
        incident = record.incident
        with incident_context(incident_id):
            self.store.update(incident_id, status="processing")
            try:
                async with asyncio.timeout(self._timeout):
                    classification, own_usage = await self._classify(incident)
                    self.store.update(
                        incident_id, classification=classification,
                        usage_by_agent={"orchestrator": own_usage},
                    )
                    category = classification.category
                    if category not in self.specialists:
                        self.store.update(incident_id, status="no_specialist")
                        logger.info("no specialist for category %s", category)
                        return

                    investigation, delegate_usage, mode = await self._delegate(incident, category)
                    status = (
                        "awaiting_approval"
                        if investigation.proposal.action in CHANGE_ACTIONS
                        else "completed"
                    )
                    self.store.update(
                        incident_id,
                        status=status,
                        investigation=investigation,
                        delegation=mode,
                        usage_by_agent={
                            "orchestrator": own_usage + delegate_usage,
                            SPECIALIST_AGENTS[category]: investigation.usage,
                        },
                    )
                    logger.info("incident %s finished with status %s", incident_id, status)
            except TimeoutError:
                self._fail(incident_id, f"incident exceeded its {self._timeout:g} s limit")
            except Exception as exc:
                logger.exception("incident %s failed", incident_id)
                self._fail(incident_id, f"{type(exc).__name__}: {exc}")

    def _fail(self, incident_id: str, error: str) -> None:
        self.store.update(incident_id, status="failed", error=error[:500])
