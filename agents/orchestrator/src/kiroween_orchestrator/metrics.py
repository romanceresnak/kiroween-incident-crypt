"""Orchestrator metrics for the Grafana dashboards (memory-and-ui REQ-MON-05)."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

from kiroween_shared.metrics import REGISTRY

INCIDENTS = Counter(
    "orchestrator_incidents",
    "Incidents by final category, status and investigation outcome",
    ["category", "status", "outcome"],
    registry=REGISTRY,
)
INVESTIGATION_SECONDS = Histogram(
    "orchestrator_investigation_seconds",
    "Time from the start of processing to the end of the investigation",
    ["category"],
    buckets=(5, 10, 20, 30, 45, 60, 90, 120, 180, 300),
    registry=REGISTRY,
)


def record_incident(category: str | None, status: str, outcome: str | None) -> None:
    INCIDENTS.labels(category=category or "none", status=status,
                     outcome=outcome or "none").inc()

APPROVALS = Counter(
    "orchestrator_approvals",
    "Human decisions on proposals, and how the PR was delivered",
    ["decision", "delivery"],
    registry=REGISTRY,
)

LEARNED = Counter(
    "orchestrator_learned",
    "Approved incidents turned into postmortems, by outcome (disabled, error, "
    "written_started, written_already_running, written_failed)",
    ["result"],
    registry=REGISTRY,
)
