"""scenario-lab TASK-05: the orchestrator's Lab API (REQ-AP-01 to AP-05). Normal tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from kiroween_orchestrator.api import create
from kiroween_orchestrator.auth import Auth, AuthConfig
from kiroween_orchestrator.lab import catalog
from kiroween_orchestrator.lab.client import LabClient, LabError, LabUnavailable
from kiroween_orchestrator.lab.routes import primary_incident
from kiroween_orchestrator.lab.scenarios import SCENARIOS
from kiroween_orchestrator.service import IncidentService
from kiroween_shared.models import Classification, Incident, IncidentRecord

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
STARTED = (NOW - timedelta(seconds=30)).isoformat()
USER, PASSWORD = "judge", "correct horse battery staple"  # noqa: S105
HTTPS = "https://crypt.example.cloudfront.net"


async def never(*args):  # type: ignore[no-untyped-def]
    raise AssertionError("not used")


def record(iid: str, namespace: str, *, age_s: int = 10, related_to: str | None = None,
           category: str | None = "network", status: str = "awaiting_approval",
           reason: str = "Unhealthy", workload: str = "client-000001") -> IncidentRecord:
    incident = Incident.model_validate({
        "incident_id": iid, "source": "watcher", "reason": reason, "namespace": namespace,
        "workload": {"kind": "Deployment", "name": workload}, "observed_at": NOW})
    classification = (Classification(category=category, rationale="r", confidence="high")
                      if category else None)
    at = NOW - timedelta(seconds=age_s)
    return IncidentRecord(incident=incident, status=status, classification=classification,
                          related_to=related_to, created_at=at, updated_at=at)


def service(*records: IncidentRecord) -> IncidentService:
    svc = IncidentService(classify=never, delegate=never)
    for r in records:
        svc.store.put(r)
    return svc


class FakeRunner:
    """The lab-runner as the orchestrator sees it: an HTTP service."""

    def __init__(self) -> None:
        self.status_body: dict = {"state": "idle", "scenario": None, "namespace": None,
                                  "started_at": None, "expires_at": None, "runs_used": 0,
                                  "max_runs": 10}
        self.calls: list[tuple[str, str, dict | None]] = []
        self.answer: tuple[int, dict, dict] | None = None
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        if self.down:
            raise httpx.ConnectError("refused")
        if self.answer:
            status, payload, headers = self.answer
            return httpx.Response(status, json=payload, headers=headers)
        if request.url.path == "/status":
            return httpx.Response(200, json=self.status_body)
        if request.url.path == "/run":
            return httpx.Response(202, json={"run": "abc123", **(body or {})})
        return httpx.Response(200, json={**self.status_body, "state": "idle"})


def lab_client(runner: FakeRunner) -> LabClient:
    return LabClient("http://lab-runner:8080", transport=httpx.MockTransport(runner.handler))


def app(runner: FakeRunner | None, svc: IncidentService | None = None,
        login: bool = False) -> TestClient:
    auth = Auth(AuthConfig(username=USER, password=PASSWORD)) if login else None
    client = lab_client(runner) if runner else None
    return TestClient(create(svc or service(), auth, client), base_url=HTTPS)


# ----------------------------------------------------------------------------- catalogue


def test_the_scenarios_endpoint_lists_the_fourteen_cards() -> None:
    with app(FakeRunner()) as client:
        body = client.get("/lab/scenarios").json()
    assert [s["id"] for s in body] == [s.id for s in SCENARIOS]
    assert body == catalog.public()
    assert {s["crew"] for s in body} == {"Vampire", "Spider", "Zombie", "Ghost"}


def test_the_catalogue_works_even_when_the_runner_is_down() -> None:
    runner = FakeRunner()
    runner.down = True
    with app(runner) as client:
        assert client.get("/lab/scenarios").status_code == 200
    assert runner.calls == []


# ------------------------------------------------------------------------------- status


def test_status_of_an_idle_lab_has_no_incident() -> None:
    with app(FakeRunner()) as client:
        body = client.get("/lab/status").json()
    assert body["state"] == "idle" and body["incident"] is None


def test_status_links_the_incident_the_run_produced() -> None:
    runner = FakeRunner()
    runner.status_body.update(state="running", scenario="netpol-blocks-client",
                              namespace="lab-04", started_at=STARTED)
    svc = service(record("first", "lab-04", age_s=20),
                  record("second", "lab-04", age_s=5, related_to="first"),
                  record("elsewhere", "lab-05", age_s=20),
                  record("older", "lab-04", age_s=3600))
    with app(runner, svc) as client:
        body = client.get("/lab/status").json()
    assert body["incident"] == {
        "incident_id": "first", "status": "awaiting_approval", "category": "network",
        "reason": "Unhealthy", "workload": "Deployment/client-000001", "correlated": 1}
    assert body["scenario"] == "netpol-blocks-client" and body["runs_used"] == 0


def test_the_incident_is_the_oldest_uncorrelated_one_since_the_run_started() -> None:
    records = [record("late", "lab-01", age_s=2), record("early", "lab-01", age_s=25),
               record("attached", "lab-01", age_s=24, related_to="early"),
               record("before", "lab-01", age_s=300)]
    found = primary_incident(records, "lab-01", STARTED)
    assert found and found["incident_id"] == "early" and found["correlated"] == 1
    assert primary_incident(records, "lab-02", STARTED) is None
    assert primary_incident([], "lab-01", STARTED) is None
    assert primary_incident(records, "lab-01", "not a date") is None


def test_an_incident_that_is_not_classified_yet_has_no_category() -> None:
    found = primary_incident([record("x", "lab-01", category=None, status="processing")],
                             "lab-01", STARTED)
    assert found and found["category"] is None and found["status"] == "processing"


def test_clock_slack_keeps_an_incident_seen_a_moment_before_the_start_time() -> None:
    just_before = record("slack", "lab-01", age_s=31)  # one second before STARTED
    way_before = record("old", "lab-01", age_s=40)
    assert primary_incident([just_before], "lab-01", STARTED)
    assert primary_incident([way_before], "lab-01", STARTED) is None


# ------------------------------------------------------------------------------ run, reset


def test_run_sends_only_the_scenario_id_to_the_runner() -> None:
    runner = FakeRunner()
    with app(runner) as client:
        response = client.post("/lab/run", json={"scenario": "oom-killed"})
    assert response.status_code == 202 and response.json()["run"] == "abc123"
    assert runner.calls == [("POST", "/run", {"scenario": "oom-killed"})]


@pytest.mark.parametrize("body", [{}, {"scenario": ""}, {"scenario": "oom-killed", "x": 1},
                                  {"scenario": "A" * 65}, {"scenario": "Has Space"},
                                  {"scenario": 5}, ["oom-killed"]])
def test_run_refuses_anything_but_a_scenario_id(body) -> None:  # type: ignore[no-untyped-def]
    runner = FakeRunner()
    with app(runner) as client:
        assert client.post("/lab/run", json=body).status_code == 422
    assert runner.calls == []


def test_run_refuses_an_unknown_id_before_calling_the_runner() -> None:
    runner = FakeRunner()
    with app(runner) as client:
        response = client.post("/lab/run", json={"scenario": "does-not-exist"})
    assert response.status_code == 404 and response.json() == {"detail": "unknown scenario"}
    assert runner.calls == []


def test_the_runners_refusals_reach_the_judge_with_their_status_and_wait() -> None:
    runner = FakeRunner()
    runner.answer = (409, {"detail": "a scenario is already running: oom-killed"}, {})
    with app(runner) as client:
        busy = client.post("/lab/run", json={"scenario": "memory-leak"})
        assert busy.status_code == 409 and "oom-killed" in busy.json()["detail"]
        runner.answer = (429, {"detail": "the hourly limit of runs has been reached"},
                         {"retry-after": "840"})
        limited = client.post("/lab/run", json={"scenario": "memory-leak"})
    assert limited.status_code == 429 and limited.headers["retry-after"] == "840"


def test_reset_calls_the_runner() -> None:
    runner = FakeRunner()
    with app(runner) as client:
        response = client.post("/lab/reset", json={})
    assert response.status_code == 200 and response.json()["state"] == "idle"
    assert runner.calls == [("POST", "/reset", None)]


def test_a_runner_that_is_down_is_a_clear_503_everywhere() -> None:
    runner = FakeRunner()
    runner.down = True
    with app(runner) as client:
        for method, path, body in [("GET", "/lab/status", None),
                                   ("POST", "/lab/run", {"scenario": "oom-killed"}),
                                   ("POST", "/lab/reset", {})]:
            response = client.request(method, path, json=body)
            assert response.status_code == 503, path
            assert response.json() == {"detail": "the lab runner is not available right now"}
            assert "refused" not in response.text and "ConnectError" not in response.text


def test_without_a_runner_url_the_lab_says_it_is_not_configured() -> None:
    with app(None) as client:
        assert client.get("/lab/scenarios").status_code == 200
        for method, path, body in [("GET", "/lab/status", None),
                                   ("POST", "/lab/run", {"scenario": "oom-killed"}),
                                   ("POST", "/lab/reset", {})]:
            response = client.request(method, path, json=body)
            assert response.status_code == 503 and "not configured" in response.json()["detail"]


# ---------------------------------------------------------------- behind the login (AP-01)


def test_every_lab_route_needs_the_login_and_the_posts_check_the_origin() -> None:
    runner = FakeRunner()
    with app(runner, login=True) as client:
        for method, path in [("GET", "/lab/scenarios"), ("GET", "/lab/status"),
                             ("POST", "/lab/run"), ("POST", "/lab/reset")]:
            assert client.request(method, path, json={}).status_code == 401, path
        assert runner.calls == []
        client.post("/auth/login", json={"username": USER, "password": PASSWORD})
        assert client.get("/lab/scenarios").status_code == 200
        foreign = client.post("/lab/run", json={"scenario": "oom-killed"},
                              headers={"Origin": "https://evil.example.com"})
        assert foreign.status_code == 403
        simple = client.post("/lab/run", content='{"scenario": "oom-killed"}',
                             headers={"Content-Type": "text/plain"})
        assert simple.status_code in (415, 422)
        assert client.post("/lab/reset", json={},
                           headers={"Origin": "https://evil.example.com"}).status_code == 403
        assert runner.calls == []
        assert client.post("/lab/run", json={"scenario": "oom-killed"}).status_code == 202


def test_the_lab_page_is_served_by_the_ui_mount_behind_the_login() -> None:
    with app(FakeRunner(), login=True) as client:
        assert client.get("/ui/lab.html", follow_redirects=False).status_code == 302


# ----------------------------------------------------------------------- the client itself


async def test_the_client_maps_statuses_and_failures() -> None:
    runner = FakeRunner()
    client = lab_client(runner)
    assert (await client.status())["state"] == "idle"
    runner.answer = (409, {"detail": "busy"}, {})
    with pytest.raises(LabError) as busy:
        await client.run("oom-killed")
    assert busy.value.status == 409 and busy.value.detail == "busy"
    runner.answer = (429, {"detail": "slow down"}, {"retry-after": "12"})
    with pytest.raises(LabError) as limited:
        await client.run("oom-killed")
    assert limited.value.retry_after == 12
    runner.answer = (500, {}, {})
    with pytest.raises(LabError, match="500"):
        await client.reset()
    runner.answer = None
    runner.down = True
    with pytest.raises(LabUnavailable):
        await client.status()


async def test_the_client_treats_a_non_json_answer_as_unavailable() -> None:
    client = LabClient("http://x", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>")))
    with pytest.raises(LabUnavailable):
        await client.status()
