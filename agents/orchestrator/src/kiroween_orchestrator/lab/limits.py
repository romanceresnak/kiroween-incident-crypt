"""How many runs, and for how long (scenario-lab REQ-LM-02, LM-03, LM-06). In memory."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable

HOUR_S = 3600.0


class LimitReached(RuntimeError):
    def __init__(self, retry_after_s: int) -> None:
        super().__init__(f"{retry_after_s} s until the next run is allowed")
        self.retry_after_s = retry_after_s


class RunLimits:
    """A sliding hour of accepted runs. Refused runs are never recorded."""

    def __init__(self, max_per_hour: int = 10, cleanup_s: int = 900,
                 clock: Callable[[], float] = time.time) -> None:
        if max_per_hour < 1 or cleanup_s < 1:
            raise ValueError("the limits must be positive")
        self.max_per_hour = max_per_hour
        self.cleanup_s = cleanup_s
        self._clock = clock
        self._runs: deque[float] = deque()

    def _prune(self) -> None:
        now = self._clock()
        while self._runs and now - self._runs[0] >= HOUR_S:
            self._runs.popleft()

    def used(self) -> int:
        self._prune()
        return len(self._runs)

    def retry_after_s(self) -> int:
        """Seconds until a run is allowed (0 when one is allowed now)."""
        self._prune()
        if len(self._runs) < self.max_per_hour:
            return 0
        return max(1, math.ceil(self._runs[0] + HOUR_S - self._clock()))

    def check(self) -> None:
        wait = self.retry_after_s()
        if wait:
            raise LimitReached(wait)

    def record(self) -> None:
        self._runs.append(self._clock())
