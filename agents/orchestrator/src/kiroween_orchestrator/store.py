"""Bounded in-memory incident store (REQ-ORC-13). Records are lost on restart."""

from __future__ import annotations

from collections import OrderedDict

from kiroween_shared.models import IncidentRecord, utcnow


class IncidentStore:
    def __init__(self, capacity: int = 500) -> None:
        self.capacity = capacity
        self._items: OrderedDict[str, IncidentRecord] = OrderedDict()

    def get(self, incident_id: str) -> IncidentRecord | None:
        return self._items.get(incident_id)

    def put(self, record: IncidentRecord) -> None:
        key = record.incident.incident_id
        self._items[key] = record
        self._items.move_to_end(key)
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)

    def update(self, incident_id: str, **changes: object) -> IncidentRecord | None:
        record = self._items.get(incident_id)
        if record is None:
            return None
        updated = record.model_copy(update={**changes, "updated_at": utcnow()})
        self._items[incident_id] = updated
        return updated

    def records(self) -> list[IncidentRecord]:
        return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)
