"""Queue, workers and the incident lifecycle (REQ-ORC-03, ORC-05, ORC-09 to ORC-12)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal

from kiroween_shared.context import incident_context
from kiroween_shared.models import (
    Approval,
    Classification,
    Incident,
    IncidentRecord,
    Investigation,
    Learning,
    RelatedIncident,
    SimilarIncident,
    Usage,
    utcnow,
)

from .approval import Approver
from .correlation import correlate
from .learning import Learner, can_learn, error_name
from .metrics import APPROVALS, INVESTIGATION_SECONDS, LEARNED, record_incident
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


class DecisionConflict(RuntimeError):
    """The incident is not waiting for a decision (HTTP 409)."""


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
        correlation_window_s: float = 300.0,
        now: Callable[[], datetime] = utcnow,
        similar: Callable[[Incident, str | None], Awaitable[list[SimilarIncident]]] | None = None,
        approver: Approver | None = None,
        learner: Learner | None = None,
    ) -> None:
        # Categories with a configured specialist. Everything else ends as no_specialist.
        self.specialists = specialists
        self._memory = similar
        self._learner = learner
        self._approver = approver if approver is not None else Approver()
        self._correlation_window = correlation_window_s
        self._now = now
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
        now = self._now()
        found = correlate(incident, self.store.records(), now, self._correlation_window)
        if found is not None:
            # Same root cause as an open incident: attach it, no model call (REQ-COR-03).
            primary, rule = found
            record = IncidentRecord(incident=incident, status="correlated",
                                    related_to=primary.incident.incident_id,
                                    created_at=now, updated_at=now)
            self.store.put(record)
            related = RelatedIncident(
                incident_id=incident.incident_id, reason=incident.reason,
                workload=incident.workload, pod=incident.pod, match=rule,
                observed_at=incident.observed_at)
            self.store.update(primary.incident.incident_id,
                              related_incidents=[*primary.related_incidents, related])
            logger.info("incident %s (%s) correlated to %s by %s", incident.incident_id,
                        incident.reason, primary.incident.incident_id, rule)
            category = primary.classification.category if primary.classification else None
            record_incident(category, "correlated", None)
            return record, True
        if self._queue.full():
            raise QueueFull("incident queue is full")
        record = IncidentRecord(incident=incident, created_at=now, updated_at=now)
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
        started = time.monotonic()
        category: str | None = None
        with incident_context(incident_id):
            self.store.update(incident_id, status="processing")
            try:
                async with asyncio.timeout(self._timeout):
                    # The similar-incident lookup needs the category, so it follows the
                    # classification (polish REQ-RET-01): one Retrieve call after the model.
                    classification, own_usage = await self._classify(incident)
                    category = classification.category
                    self.store.update(
                        incident_id, classification=classification,
                        usage_by_agent={"orchestrator": own_usage},
                    )
                    similar = await self._similar(incident, category)
                    self.store.update(incident_id, similar_incidents=similar)
                    if category not in self.specialists:
                        self.store.update(incident_id, status="no_specialist")
                        record_incident(category, "no_specialist", None)
                        logger.info("no specialist for category %s", category)
                        return

                    # The specialist sees the hits as context (REQ-MEM-06, MEM-07).
                    with_memory = incident.model_copy(update={"similar_incidents": similar})
                    investigation, delegate_usage, mode = await self._delegate(
                        with_memory, category)
                    proposal = investigation.proposal
                    status = (
                        "awaiting_approval"
                        if proposal is not None and proposal.action in CHANGE_ACTIONS
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
                    INVESTIGATION_SECONDS.labels(category=category).observe(
                        time.monotonic() - started)
                    record_incident(category, status, investigation.outcome)
                    logger.info("incident %s finished with status %s", incident_id, status)
            except TimeoutError:
                self._fail(incident_id, f"incident exceeded its {self._timeout:g} s limit",
                           category)
            except Exception as exc:
                logger.exception("incident %s failed", incident_id)
                self._fail(incident_id, f"{type(exc).__name__}: {exc}", category)

    async def _similar(self, incident: Incident, category: str | None) -> list[SimilarIncident]:
        if self._memory is None:
            return []
        try:
            return await self._memory(incident, category)
        except Exception:  # memory is context, never a reason to fail the incident
            logger.exception("similar-incident lookup failed")
            return []

    def _fail(self, incident_id: str, error: str, category: str | None = None) -> None:
        self.store.update(incident_id, status="failed", error=error[:500])
        record_incident(category, "failed", None)

    async def _learn(self, incident_id: str, record: IncidentRecord,
                     approval: Approval) -> tuple[Approval, IncidentRecord]:
        """Turn the approved incident into a postmortem (polish REQ-LRN-05). Whatever goes
        wrong, the approval and its pull request stay as they are."""
        if self._learner is None:
            return approval, record
        try:
            with incident_context(incident_id):
                learning = await self._learner.learn(record, approval)
        except Exception as exc:  # Learner.learn does not raise; this is the second wall
            logger.exception("learning failed for %s", incident_id)
            learning = Learning(status="error", error=error_name(exc))
        result = (f"written_{learning.ingestion}" if learning.status == "written"
                  else learning.status)
        LEARNED.labels(result=result).inc()
        approval = approval.model_copy(update={"learning": learning})
        updated = self.store.update(incident_id, approval=approval)
        return approval, updated if updated is not None else record

    async def decide(self, incident_id: str, decision: Literal["approved", "rejected"],
                     by: str, comment: str = "") -> IncidentRecord:
        """Record a human decision (REQ-APR-01). Raises KeyError / DecisionConflict."""
        record = self.store.get(incident_id)
        if record is None:
            raise KeyError(incident_id)
        if record.status != "awaiting_approval":
            raise DecisionConflict(f"incident is {record.status}, not awaiting_approval")
        approval = Approval(decision=decision, by=by, comment=comment, at=self._now())
        # Claim the decision before any await, so a second click cannot open a second PR.
        record = self.store.update(incident_id, status=decision, approval=approval)
        if decision == "approved" and self._approver is not None and record is not None:
            with incident_context(incident_id):
                pr = await self._approver.pull_request(record, approval)
            approval = approval.model_copy(update={"pull_request": pr})
            record = self.store.update(incident_id, approval=approval)
        if (decision == "approved" and self._learner is not None and record is not None
                and can_learn(record)):
            approval, record = await self._learn(incident_id, record, approval)
        delivery = "none"
        if approval.pull_request is not None:
            pr = approval.pull_request
            delivery = "error" if pr.error else pr.mode
        APPROVALS.labels(decision=decision, delivery=delivery).inc()
        logger.info("incident %s %s by %s (%s)", incident_id, decision, by, delivery)
        return record  # type: ignore[return-value]
