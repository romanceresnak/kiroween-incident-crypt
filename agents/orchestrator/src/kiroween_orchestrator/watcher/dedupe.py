"""Suppress repeat incidents (REQ-WAT-04)."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable


class Cooldown:
    """Allow a key at most once per window. Keys are pruned as time passes."""

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.seconds = seconds
        self._clock = clock
        self._last: dict[Hashable, float] = {}

    def allow(self, key: Hashable) -> bool:
        now = self._clock()
        self._last = {k: t for k, t in self._last.items() if now - t < self.seconds}
        if key in self._last:
            return False
        self._last[key] = now
        return True


class SeenIds:
    """Bounded set of incident ids already handled."""

    def __init__(self, capacity: int = 2000) -> None:
        self.capacity = capacity
        self._ids: OrderedDict[str, None] = OrderedDict()

    def __contains__(self, incident_id: object) -> bool:
        return incident_id in self._ids

    def add(self, incident_id: str) -> None:
        self._ids[incident_id] = None
        self._ids.move_to_end(incident_id)
        while len(self._ids) > self.capacity:
            self._ids.popitem(last=False)
