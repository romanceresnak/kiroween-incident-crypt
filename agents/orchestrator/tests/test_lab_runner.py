"""scenario-lab TASK-05: the runner, the limits and the cleanup (REQ-RN, REQ-LM). Normal tests.

The runner is driven through a fake clock and a fake cluster, and every background step runs
inline (``spawn``), so nothing sleeps and nothing is racy.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kiroween_orchestrator.lab.limits import LimitReached, RunLimits
from kiroween_orchestrator.lab.render import LABEL_LAB
from kiroween_orchestrator.lab.runner import (
    Busy,
    LabRunner,
    NotReady,
    RateLimited,
    RunnerError,
    UnknownScenario,
)
from kiroween_orchestrator.lab.scenarios import SCENARIOS
from kiroween_orchestrator.lab.server import create


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class FakeKube:
    """A cluster that remembers what was applied, per namespace."""

    def __init__(self) -> None:
        self.objects: dict[str, list[dict]] = {}
        self.ready_after: dict[str, int] = {}   # deployment name -> polls until ready
        self.polls: dict[str, int] = {}
        self.calls: list[tuple] = []
        self.missing: set[str] = set()
        self.fail_apply_at: int | None = None
        self.stubborn = 0            # delete_all rounds that leave one object behind
        self.on_apply = None

    def namespace_ready(self, namespace: str) -> bool:
        self.calls.append(("namespace_ready", namespace))
        return namespace not in self.missing

    def apply(self, obj: dict) -> None:
        ns = obj["metadata"]["namespace"]
        self.calls.append(("apply", ns, obj["kind"], obj["metadata"]["name"]))
        if self.fail_apply_at is not None and len(self.objects.get(ns, [])) == self.fail_apply_at:
            raise RuntimeError("boom")
        if self.on_apply:
            self.on_apply(obj)
        self.objects.setdefault(ns, []).append(obj)

    def deployment_ready(self, namespace: str, name: str) -> bool:
        self.polls[name] = self.polls.get(name, 0) + 1
        return self.polls[name] > self.ready_after.get(name, 0)

    def delete_all(self, namespace: str) -> None:
        self.calls.append(("delete_all", namespace))
        if self.stubborn > 0:
            self.stubborn -= 1
            self.objects[namespace] = self.objects.get(namespace, [])[:1]
            return
        self.objects[namespace] = []

    def is_empty(self, namespace: str) -> bool:
        return not self.objects.get(namespace)

    def applied(self, namespace: str) -> list[str]:
        return [f"{o['kind']}/{o['metadata']['name']}" for o in self.objects.get(namespace, [])]


def make(**kw):  # type: ignore[no-untyped-def]
    clock = Clock()
    kube = FakeKube()
    limits = RunLimits(kw.pop("max_per_hour", 10), kw.pop("cleanup_s", 900), clock)
    runs = iter(f"{n:06x}" for n in range(1, 100))
    runner = LabRunner(kube, limits, clock=clock, sleep=clock.sleep,
                       new_run=lambda: next(runs), spawn=lambda fn: fn(), **kw)
    return runner, kube, clock


# ------------------------------------------------------------------------------ limits


def test_limits_allow_ten_runs_an_hour_and_name_when_the_next_is_allowed() -> None:
    clock = Clock()
    limits = RunLimits(10, 900, clock)
    for _ in range(10):
        limits.check()
        limits.record()
        clock.now += 60
    assert limits.used() == 10
    with pytest.raises(LimitReached) as caught:
        limits.check()
    assert caught.value.retry_after_s == 3600 - 600
    clock.now += caught.value.retry_after_s
    limits.check()
    assert limits.used() == 9


def test_refused_runs_do_not_count() -> None:
    limits = RunLimits(2, 900, Clock())
    for _ in range(5):
        try:
            limits.check()
            limits.record()
        except LimitReached:
            pass
    assert limits.used() == 2


def test_limits_must_be_positive() -> None:
    for kw in ({"max_per_hour": 0}, {"cleanup_s": 0}, {"max_per_hour": -1}):
        with pytest.raises(ValueError, match="positive"):
            RunLimits(**kw)


def test_the_defaults_are_ten_an_hour_and_fifteen_minutes() -> None:
    limits = RunLimits()
    assert (limits.max_per_hour, limits.cleanup_s) == (10, 900)


# -------------------------------------------------------------------------------- start


def test_at_start_every_lab_namespace_is_cleaned_and_the_runner_is_idle() -> None:
    runner, kube, _ = make()
    kube.objects = {"lab-03": [{"stale": True}]}
    assert runner.started is False
    runner.start()
    cleaned = [c[1] for c in kube.calls if c[0] == "delete_all"]
    assert cleaned == [f"lab-{n:02d}" for n in range(1, 15)]
    assert runner.started and runner.status()["state"] == "idle"
    assert kube.objects["lab-03"] == []


def test_a_namespace_that_is_missing_is_skipped_not_fatal() -> None:
    runner, kube, _ = make()
    kube.missing = {"lab-05"}
    runner.start()
    assert "lab-05" not in [c[1] for c in kube.calls if c[0] == "delete_all"]
    assert runner.started


def test_a_failing_clean_at_start_does_not_stop_the_runner() -> None:
    runner, kube, _ = make()

    def explode(namespace: str) -> None:
        raise RuntimeError("api down")

    kube.delete_all = explode  # type: ignore[method-assign]
    runner.start()
    assert runner.started and runner.status()["state"] == "idle"


# --------------------------------------------------------------------------------- run


def test_a_run_applies_the_scenario_in_its_own_namespace() -> None:
    runner, kube, clock = make()
    info = runner.run("oom-killed")
    assert info["scenario"] == "oom-killed" and info["namespace"] == "lab-01"
    assert info["run"] == "000001"
    assert kube.applied("lab-01") == ["Deployment/leaker-000001"]
    status = runner.status()
    assert status["state"] == "running" and status["scenario"] == "oom-killed"
    assert status["run"] == "000001" and status["namespace"] == "lab-01"
    assert status["note"] == "applied; the crew is watching"
    assert status["runs_used"] == 1 and status["max_runs"] == 10
    assert status["started_at"] < status["expires_at"]
    assert clock.now == 1_700_000_000.0


def test_every_scenario_runs_in_the_namespace_its_card_names() -> None:
    for scenario in SCENARIOS:
        runner, kube, _ = make()
        runner.run(scenario.id)
        assert [ns for ns, objs in kube.objects.items() if objs] == [scenario.namespace]
        assert all(o["metadata"]["namespace"] == scenario.namespace
                   for o in kube.objects[scenario.namespace])
        assert all(o["metadata"]["labels"][LABEL_LAB] == "true"
                   for o in kube.objects[scenario.namespace])


def test_only_the_catalogue_can_be_run() -> None:
    runner, kube, _ = make()
    for bad in ("", "nope", "lab-01", "../x", "OOM-KILLED", "oom-killed;rm"):
        with pytest.raises(UnknownScenario):
            runner.run(bad)
    assert kube.objects == {} and runner.status()["state"] == "idle"
    assert runner.status()["runs_used"] == 0


def test_a_scenario_with_two_phases_waits_for_the_working_revision() -> None:
    runner, kube, clock = make()
    kube.ready_after = {"web-000001": 3}
    runner.run("bad-image-tag")
    assert [c for c in kube.calls if c[0] == "apply"] == [
        ("apply", "lab-08", "Deployment", "web-000001"),
        ("apply", "lab-08", "Deployment", "web-000001")]
    assert kube.polls["web-000001"] == 4
    assert clock.slept == [2.0, 2.0, 2.0]
    first, second = kube.objects["lab-08"]
    assert first["spec"]["template"]["spec"]["containers"][0]["image"].endswith("3.12-slim")
    assert second["spec"]["template"]["spec"]["containers"][0]["image"].endswith("-gone")


def test_waiting_for_the_working_revision_gives_up_after_ninety_seconds() -> None:
    runner, kube, clock = make()
    kube.ready_after = {"shop-000001": 10_000}
    runner.run("rollout-stuck-readiness")
    assert len(kube.objects["lab-11"]) == 2, "the broken revision is applied anyway"
    assert 90 <= sum(clock.slept) < 100


def test_an_apply_error_is_reported_and_the_run_can_be_reset() -> None:
    runner, kube, _ = make()
    kube.fail_apply_at = 1
    runner.run("netpol-blocks-client")
    status = runner.status()
    assert status["state"] == "running"
    assert status["note"] == "could not apply the scenario (RuntimeError)"
    runner.reset()
    assert runner.status()["state"] == "idle" and kube.objects["lab-04"] == []


# -------------------------------------------------------------------------- one at a time


def test_a_second_run_is_refused_while_one_is_active_and_names_it() -> None:
    runner, kube, _ = make()
    runner.run("oom-killed")
    with pytest.raises(Busy) as caught:
        runner.run("memory-leak")
    assert caught.value.scenario == "oom-killed" and caught.value.status == 409
    assert "oom-killed" in str(caught.value)
    assert kube.objects.get("lab-03", []) == []
    assert runner.status()["runs_used"] == 1, "a refused run does not count"


def test_a_run_is_refused_while_the_namespace_is_cleaned_up() -> None:
    runner, kube, _ = make()
    runner.run("oom-killed")
    runner._state = "cleaning"  # noqa: SLF001 (what a background cleanup looks like)
    with pytest.raises(Busy, match="cleaned up"):
        runner.run("memory-leak")


def test_a_namespace_with_leftovers_is_cleaned_before_it_is_used_again() -> None:
    runner, kube, _ = make()
    kube.objects["lab-01"] = [{"kind": "Deployment", "metadata": {"name": "old"}}]
    with pytest.raises(Busy, match="cleaned up"):
        runner.run("oom-killed")
    assert ("delete_all", "lab-01") in kube.calls, "the refusal also tries to clean"
    assert runner.status()["runs_used"] == 0
    runner.run("oom-killed")  # the leftover is gone now
    assert kube.applied("lab-01") == ["Deployment/leaker-000001"]


def test_a_namespace_that_is_not_a_lab_namespace_is_refused() -> None:
    runner, kube, _ = make()
    kube.missing = {"lab-01"}
    with pytest.raises(NotReady, match="lab-01"):
        runner.run("oom-killed")
    assert runner.status()["state"] == "idle" and runner.status()["runs_used"] == 0


# ----------------------------------------------------------------------- ten an hour


def test_the_eleventh_run_in_an_hour_is_refused_with_the_wait() -> None:
    runner, kube, clock = make()
    for _ in range(10):
        runner.run("oom-killed")
        runner.reset()
        clock.now += 30
    with pytest.raises(RateLimited) as caught:
        runner.run("oom-killed")
    assert caught.value.status == 429
    assert caught.value.retry_after_s == 3600 - 300
    assert runner.status()["runs_used"] == 10
    assert runner.status()["next_allowed_in_s"] == 3300
    clock.now += 3300
    runner.run("oom-killed")


def test_the_limit_is_configuration() -> None:
    runner, _, _ = make(max_per_hour=2)
    runner.run("oom-killed")
    runner.reset()
    runner.run("oom-killed")
    runner.reset()
    with pytest.raises(RateLimited):
        runner.run("oom-killed")
    assert runner.status()["max_runs"] == 2


# ---------------------------------------------------------------------- cleanup (LM-03..05)


def test_fifteen_minutes_after_the_start_the_run_is_cleaned_without_a_request() -> None:
    runner, kube, clock = make()
    runner.run("oom-killed")
    clock.now += 899
    runner.tick()
    assert runner.status()["state"] == "running" and kube.objects["lab-01"]
    clock.now += 1
    runner.tick()
    status = runner.status()
    assert status["state"] == "idle" and kube.objects["lab-01"] == []
    assert status["scenario"] is None and status["started_at"] is None
    assert ("delete_all", "lab-01") in kube.calls


def test_the_cleanup_time_is_configuration() -> None:
    runner, kube, clock = make(cleanup_s=60)
    runner.run("oom-killed")
    assert runner.status()["cleanup_seconds"] == 60
    clock.now += 60
    runner.tick()
    assert runner.status()["state"] == "idle"


def test_ticking_with_nothing_running_does_nothing() -> None:
    runner, kube, clock = make()
    clock.now += 10_000
    runner.tick()
    assert kube.calls == [] and runner.status()["state"] == "idle"


def test_reset_deletes_now_and_returns_to_idle() -> None:
    runner, kube, _ = make()
    runner.run("memory-leak")
    status = runner.reset()
    assert status["state"] == "idle" and kube.objects["lab-03"] == []
    assert runner.status()["scenario"] is None


def test_reset_with_nothing_running_succeeds() -> None:
    runner, kube, _ = make()
    assert runner.reset()["state"] == "idle"
    assert [c for c in kube.calls if c[0] == "delete_all"] == []


def test_cleanup_waits_until_the_namespace_is_empty_and_deletes_again_while_it_is_not() -> None:
    runner, kube, clock = make()
    runner.run("oom-killed")
    kube.stubborn = 3  # three delete rounds leave an object terminating
    runner.reset()
    deletes = [c for c in kube.calls if c == ("delete_all", "lab-01")]
    assert len(deletes) == 4 and kube.objects["lab-01"] == []
    assert clock.slept == [2.0, 2.0, 2.0]


def test_cleanup_gives_up_waiting_after_ninety_seconds_but_the_next_run_heals() -> None:
    runner, kube, clock = make()
    runner.run("oom-killed")
    kube.stubborn = 10_000
    runner.reset()
    assert runner.status()["state"] == "idle", "it must not stay stuck"
    assert 90 <= sum(clock.slept) < 100
    assert kube.objects["lab-01"], "something still terminates"
    kube.stubborn = 0
    with pytest.raises(Busy):
        runner.run("oom-killed")  # the refusal deletes again
    runner.run("oom-killed")


def test_an_error_while_cleaning_still_ends_in_idle() -> None:
    runner, kube, _ = make()
    runner.run("oom-killed")

    def explode(namespace: str) -> None:
        raise RuntimeError("api down")

    kube.delete_all = explode  # type: ignore[method-assign]
    runner.reset()
    assert runner.status()["state"] == "idle"


def test_a_reset_during_the_apply_stops_the_remaining_objects() -> None:
    """Cleanup runs in another thread and waits for an apply that has started, so the test
    defers it and runs it after _apply returns, the order it has in the running service."""
    from kiroween_orchestrator.lab.render import render

    runner, kube, _ = make()
    deferred: list = []
    runner._spawn = deferred.append  # noqa: SLF001 (hold every background step)
    info = runner.run("netpol-blocks-client")
    assert len(deferred) == 1  # the apply, not yet run
    phases = render("netpol-blocks-client", info["run"])

    def reset_after_first(obj: dict) -> None:
        kube.on_apply = None
        runner.reset()  # sets "cleaning" and defers the delete

    kube.on_apply = reset_after_first
    runner._apply(info["run"], phases)  # noqa: SLF001
    assert len([c for c in kube.calls if c[0] == "apply"]) == 1, "no object after the reset"
    assert runner.status()["state"] == "cleaning"
    assert len(deferred) == 2
    deferred[1]()  # the cleanup now deletes what the first apply created
    assert kube.objects["lab-04"] == [] and runner.status()["state"] == "idle"


def test_the_cleanup_waits_for_an_apply_that_is_in_progress() -> None:
    import threading

    runner, kube, _ = make()
    runner._spawn = lambda fn: None  # noqa: SLF001
    runner.run("oom-killed")
    entered, release = threading.Event(), threading.Event()

    def slow_apply(obj: dict) -> None:
        entered.set()
        assert release.wait(5)

    kube.on_apply = slow_apply
    from kiroween_orchestrator.lab.render import render

    worker = threading.Thread(target=runner._apply,  # noqa: SLF001
                              args=("000001", render("oom-killed", "000001")))
    worker.start()
    assert entered.wait(5)
    runner._state = "cleaning"  # noqa: SLF001
    cleaner = threading.Thread(target=runner._cleanup, args=("lab-01",))  # noqa: SLF001
    cleaner.start()
    cleaner.join(0.3)
    assert cleaner.is_alive(), "the delete must wait for the apply"
    assert ("delete_all", "lab-01") not in kube.calls
    release.set()
    worker.join(5)
    cleaner.join(5)
    assert not worker.is_alive() and not cleaner.is_alive()
    assert kube.objects["lab-01"] == [] and runner.status()["state"] == "idle"


def test_a_stale_run_cannot_apply_after_a_new_one_started() -> None:
    runner, kube, _ = make()
    runner._spawn = lambda fn: None  # noqa: SLF001
    first = runner.run("oom-killed")
    runner._state = "idle"  # noqa: SLF001
    runner.run("memory-leak")
    from kiroween_orchestrator.lab.render import render

    runner._apply(first["run"], render("oom-killed", first["run"]))  # noqa: SLF001
    assert [c for c in kube.calls if c[0] == "apply"] == []


# ------------------------------------------------------------------------------ the API


def api(runner: LabRunner) -> TestClient:
    return TestClient(create(runner))


def test_the_runner_api_has_three_routes_and_reports_ready_after_start() -> None:
    runner, _, _ = make()
    with api(runner) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503
        runner.start()
        assert client.get("/readyz").status_code == 200
        assert client.get("/status").json()["state"] == "idle"
        routes = {(r.path, tuple(sorted(r.methods or []))) for r in client.app.routes
                  if hasattr(r, "methods")}
        assert ("/run", ("POST",)) in routes and ("/reset", ("POST",)) in routes
        assert not [p for p, _ in routes if p not in {"/healthz", "/readyz", "/status", "/run",
                                                       "/reset"}]


def test_the_runner_api_runs_resets_and_maps_errors() -> None:
    runner, kube, _ = make(max_per_hour=2)
    with api(runner) as client:
        runner.start()
        ok = client.post("/run", json={"scenario": "oom-killed"})
        assert ok.status_code == 202 and ok.json()["namespace"] == "lab-01"
        busy = client.post("/run", json={"scenario": "memory-leak"})
        assert busy.status_code == 409 and busy.json()["scenario"] == "oom-killed"
        assert client.post("/reset").json()["state"] == "idle"
        assert client.post("/run", json={"scenario": "nope"}).status_code == 404
        assert client.post("/run", json={"scenario": "oom-killed"}).status_code == 202
        client.post("/reset")
        limited = client.post("/run", json={"scenario": "oom-killed"})
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == str(limited.json()["retry_after"])


def test_the_runner_api_accepts_nothing_but_a_scenario_id() -> None:
    runner, kube, _ = make()
    with api(runner) as client:
        runner.start()
        for body in ({}, {"scenario": ""}, {"scenario": "oom-killed", "namespace": "kube-system"},
                     {"scenario": "x" * 65}, {"scenario": "OOM_KILLED"}, {"scenario": 1},
                     {"manifest": "kind: Pod"}, [], "oom-killed"):
            assert client.post("/run", json=body).status_code == 422, body
        assert client.post("/run", content="oom-killed").status_code == 422
        assert kube.objects == {} or not any(kube.objects.values())


def test_a_scenario_that_cannot_render_is_a_generic_error_and_is_not_counted(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from kiroween_orchestrator.lab import runner as runner_module
    from kiroween_orchestrator.lab.render import RenderError

    def broken(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RenderError("secret detail that must not reach the client")

    monkeypatch.setattr(runner_module, "render", broken)
    runner, kube, _ = make()
    with api(runner) as client:
        runner.start()
        response = client.post("/run", json={"scenario": "oom-killed"})
        assert response.status_code == 500
        assert "secret detail" not in response.text
        assert response.json() == {"detail": "the scenario could not be prepared"}
    assert runner.status()["runs_used"] == 0 and runner.status()["state"] == "idle"
    assert issubclass(RunnerError, Exception)
