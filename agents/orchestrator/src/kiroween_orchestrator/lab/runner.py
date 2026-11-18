"""The lab-runner's state machine (scenario-lab REQ-RN-04 to RN-07, REQ-LM).

It knows nothing about Kubernetes: everything it does to a cluster goes through ``KubeWriter``,
which kube.py implements and the tests fake. One scenario runs at a time; it is cleaned up when
15 minutes have passed or when somebody presses Reset.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from .limits import LimitReached, RunLimits
from .render import RenderedPhase, RenderError, render
from .scenarios import BY_ID, SCENARIOS, Obj

logger = logging.getLogger(__name__)

State = Literal["idle", "running", "cleaning"]


class KubeWriter(Protocol):
    def namespace_ready(self, namespace: str) -> bool: ...
    def apply(self, obj: Obj) -> None: ...
    def deployment_ready(self, namespace: str, name: str) -> bool: ...
    def delete_all(self, namespace: str) -> None: ...
    def is_empty(self, namespace: str) -> bool: ...


class RunnerError(Exception):
    status = 500


class UnknownScenario(RunnerError):
    status = 404


class Busy(RunnerError):
    status = 409

    def __init__(self, scenario: str | None, state: str) -> None:
        super().__init__(f"a scenario is already {state}" + (f": {scenario}" if scenario else ""))
        self.scenario = scenario


class NotReady(RunnerError):
    status = 503


class RateLimited(RunnerError):
    status = 429

    def __init__(self, retry_after_s: int) -> None:
        super().__init__("the hourly limit of runs has been reached")
        self.retry_after_s = retry_after_s


def iso(epoch: float | None) -> str | None:
    return None if epoch is None else datetime.fromtimestamp(epoch, UTC).isoformat()


def spawn_thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="lab", daemon=True).start()


class LabRunner:
    def __init__(
        self,
        kube: KubeWriter,
        limits: RunLimits | None = None,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        new_run: Callable[[], str] = lambda: secrets.token_hex(3),
        spawn: Callable[[Callable[[], None]], None] = spawn_thread,
        wait_ready_s: float = 90.0,
        cleanup_wait_s: float = 90.0,
        poll_s: float = 2.0,
    ) -> None:
        self._kube = kube
        self._limits = limits or RunLimits(clock=clock)
        self._clock = clock
        self._sleep = sleep
        self._new_run = new_run
        self._spawn = spawn
        self._wait_ready_s = wait_ready_s
        self._cleanup_wait_s = cleanup_wait_s
        self._poll_s = poll_s
        self._lock = threading.RLock()
        self._apply_lock = threading.Lock()
        self._state: State = "idle"
        self._scenario: str | None = None
        self._namespace: str | None = None
        self._run: str | None = None
        self._started: float | None = None
        self._expires: float | None = None
        self._phase = ""
        self._note = ""
        self.started = False

    # --- reading -------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state, "scenario": self._scenario, "namespace": self._namespace,
                "run": self._run, "started_at": iso(self._started),
                "expires_at": iso(self._expires), "phase": self._phase, "note": self._note,
                "runs_used": self._limits.used(), "max_runs": self._limits.max_per_hour,
                "cleanup_seconds": self._limits.cleanup_s,
                "next_allowed_in_s": self._limits.retry_after_s(),
            }

    # --- starting ------------------------------------------------------------------------

    def start(self) -> None:
        """Clean every lab namespace, then report idle (REQ-RN-07)."""
        for scenario in SCENARIOS:
            try:
                if self._kube.namespace_ready(scenario.namespace):
                    self._kube.delete_all(scenario.namespace)
                else:
                    logger.warning("namespace %s is missing or not a lab namespace",
                                   scenario.namespace)
            except Exception:
                logger.exception("could not clean %s at start", scenario.namespace)
        with self._lock:
            self._state = "idle"
            self.started = True

    # --- run -----------------------------------------------------------------------------

    def run(self, scenario_id: str) -> dict[str, Any]:
        scenario = BY_ID.get(scenario_id)
        if scenario is None:
            raise UnknownScenario(f"unknown scenario {scenario_id!r}")
        with self._lock:
            if self._state != "idle":
                raise Busy(self._scenario,
                           "running" if self._state == "running" else "being cleaned up")
            try:
                self._limits.check()
            except LimitReached as exc:
                raise RateLimited(exc.retry_after_s) from exc
            if not self._kube.namespace_ready(scenario.namespace):
                raise NotReady(f"{scenario.namespace} is not a lab namespace yet")
            if not self._kube.is_empty(scenario.namespace):
                try:
                    self._kube.delete_all(scenario.namespace)  # self-heal a leftover
                except Exception:
                    logger.exception("could not clean %s", scenario.namespace)
                raise Busy(scenario.id, "being cleaned up")
            run = self._new_run()
            try:
                phases = render(scenario, run)
            except RenderError as exc:
                logger.error("scenario %s does not render: %s", scenario.id, exc)
                raise RunnerError("the scenario could not be prepared") from exc
            self._limits.record()
            now = self._clock()
            self._state = "running"
            self._scenario, self._namespace, self._run = scenario.id, scenario.namespace, run
            self._started, self._expires = now, now + self._limits.cleanup_s
            self._phase, self._note = f"0/{len(phases)}", "starting"
            logger.info("run %s of %s started in %s", run, scenario.id, scenario.namespace)
            self._spawn(lambda: self._apply(run, phases))
            return {"run": run, "scenario": scenario.id, "namespace": scenario.namespace,
                    "started_at": iso(now), "expires_at": iso(self._expires)}

    def _current(self, run: str) -> bool:
        return self._state == "running" and self._run == run

    def _apply(self, run: str, phases: tuple[RenderedPhase, ...]) -> None:
        try:
            for index, phase in enumerate(phases, start=1):
                if phase.wait_for and not self._wait_ready(run, phase.wait_for):
                    return
                with self._lock:
                    if not self._current(run):
                        return
                    self._phase = f"{index}/{len(phases)}"
                    self._note = f"applying step {index} of {len(phases)}"
                for obj in phase.objects:
                    # An object is applied only while this run is current, and cleanup waits
                    # for an apply that has started, so nothing is created after the delete.
                    with self._apply_lock:
                        with self._lock:
                            if not self._current(run):
                                return
                        self._kube.apply(obj)
            with self._lock:
                if self._current(run):
                    self._note = "applied; the crew is watching"
        except Exception as exc:
            logger.exception("applying run %s failed", run)
            with self._lock:
                if self._current(run):
                    self._note = f"could not apply the scenario ({type(exc).__name__})"

    def _wait_ready(self, run: str, name: str) -> bool:
        """Wait for the working revision before it is broken. False if the run ended."""
        namespace = self._namespace or ""
        deadline = self._clock() + self._wait_ready_s
        while True:
            with self._lock:
                if not self._current(run):
                    return False
                self._note = f"waiting for {name} to be ready"
            try:
                if self._kube.deployment_ready(namespace, name):
                    return True
            except Exception:
                logger.exception("reading %s failed", name)
            if self._clock() >= deadline:
                logger.warning("%s was not ready after %.0f s; going on", name,
                               self._wait_ready_s)
                return True
            self._sleep(self._poll_s)

    # --- cleanup -------------------------------------------------------------------------

    def tick(self) -> None:
        """Called every few seconds: end a run that has lived its 15 minutes (REQ-LM-03)."""
        with self._lock:
            expired = (self._state == "running" and self._expires is not None
                       and self._clock() >= self._expires)
        if expired:
            logger.info("run %s expired", self._run)
            self._begin_cleanup()

    def reset(self) -> dict[str, Any]:
        """Delete what the current run created now (REQ-LM-04). Fine when nothing runs."""
        self._begin_cleanup()
        return self.status()

    def _begin_cleanup(self) -> None:
        with self._lock:
            if self._state != "running":
                return
            self._state = "cleaning"
            self._note = "cleaning up"
            namespace = self._namespace or ""
            self._spawn(lambda: self._cleanup(namespace))

    def _cleanup(self, namespace: str) -> None:
        try:
            with self._apply_lock:
                pass  # an apply that had already started finishes before the delete
            deadline = self._clock() + self._cleanup_wait_s
            while True:
                self._kube.delete_all(namespace)  # again each round: terminating objects linger
                if self._kube.is_empty(namespace):
                    break
                if self._clock() >= deadline:
                    logger.warning("%s still holds lab objects after %.0f s", namespace,
                                   self._cleanup_wait_s)
                    break
                self._sleep(self._poll_s)
        except Exception:
            logger.exception("cleaning %s failed", namespace)
        finally:
            with self._lock:
                self._state = "idle"
                self._note = ""
                self._phase = ""
                self._started = self._expires = None
                self._scenario = self._namespace = self._run = None
