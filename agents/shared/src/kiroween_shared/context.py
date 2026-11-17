"""Per-request context: which incident the current work belongs to (REQ-MET-04)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

NO_INCIDENT = "none"

incident_id_var: ContextVar[str] = ContextVar("incident_id", default=NO_INCIDENT)


def current_incident_id() -> str:
    return incident_id_var.get()


@contextmanager
def incident_context(incident_id: str) -> Iterator[None]:
    """Attribute logs and token usage inside the block to ``incident_id``."""
    token = incident_id_var.set(incident_id)
    try:
        yield
    finally:
        incident_id_var.reset(token)
