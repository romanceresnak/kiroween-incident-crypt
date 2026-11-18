"""scenario-lab TASK-07: sandbox approvals (REQ-SB-01 to SB-05). Normal tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from kiroween_orchestrator.approval import (
    DEFAULT_LAB_BRANCH,
    Approver,
    GitHubClient,
    build_lab_client,
)
from kiroween_orchestrator.learning import Learner
from kiroween_orchestrator.service import LAB_NOT_LEARNED, IncidentService
from kiroween_shared.models import (
    Approval,
    Classification,
    Diagnosis,
    Evidence,
    Incident,
    IncidentRecord,
    Investigation,
    Learning,
    Proposal,
    Target,
)

WHEN = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
APPROVAL = Approval(decision="approved", by="Judge", at=WHEN)
REPO = "romanceresnak/kiroween-incident-crypt"
API = f"/repos/{REPO}"


def record(iid: str, namespace: str) -> IncidentRecord:
    incident = Incident.model_validate({
        "incident_id": iid, "source": "watcher", "reason": "Unhealthy", "namespace": namespace,
        "workload": {"kind": "Deployment", "name": "client-000001"}, "observed_at": WHEN,
        "message": "Readiness probe failed"})
    proposal = Proposal(
        action="set_field",
        target=Target(namespace=namespace, kind="NetworkPolicy", name="allow-trusted"),
        field="spec.ingress[0].from[0].podSelector.matchLabels.app",
        current="trusted", proposed="client", rationale="because", risk="low")
    inv = Investigation(diagnosis=Diagnosis(root_cause="root", confidence="high", evidence=[
        Evidence(tool="get_x", field="f", value="v")]), proposal=proposal)
    return IncidentRecord(incident=incident, status="awaiting_approval", investigation=inv,
                          classification=Classification(category="network", rationale="r"))


LAB = record("lab-1", "lab-04")
REAL = record("real-1", "chaos-netpol")


class FakeGitHub:
    """Records every call. ``lab_branch_exists`` decides whether the sandbox branch is there."""

    def __init__(self, *, lab_branch_exists: bool = True) -> None:
        self.lab_branch_exists = lab_branch_exists
        self.calls: list[tuple[str, str, dict | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else None
        method, path = request.method, request.url.path
        self.calls.append((method, path, payload))
        if method == "GET" and path == f"{API}/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "mainsha"}})
        if method == "GET" and path == f"{API}/git/ref/heads/lab-remediations":
            if self.lab_branch_exists:
                return httpx.Response(200, json={"object": {"sha": "labsha"}})
            return httpx.Response(404, json={"message": "Not Found"})
        if method == "POST" and path == f"{API}/git/refs":
            if payload and payload["ref"] == "refs/heads/lab-remediations":
                self.lab_branch_exists = True
            return httpx.Response(201, json={})
        if method == "GET" and path.startswith(f"{API}/contents/"):
            return httpx.Response(404, json={"message": "Not Found"})
        if method == "PUT" and path.startswith(f"{API}/contents/"):
            return httpx.Response(201, json={})
        if method == "POST" and path == f"{API}/pulls":
            return httpx.Response(201, json={"html_url": f"https://github.com/{REPO}/pull/9"})
        return httpx.Response(500, json={"message": f"unexpected {method} {path}"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def writes(self) -> list[tuple[str, str, dict | None]]:
        return [c for c in self.calls if c[0] in ("POST", "PUT", "PATCH", "DELETE")]

    def pull_requests(self) -> list[dict]:
        return [c[2] for c in self.calls if c[0] == "POST" and c[1] == f"{API}/pulls" and c[2]]


def lab_client(gh: FakeGitHub, branch: str = "lab-remediations") -> GitHubClient:
    client = build_lab_client("ghp_secret", REPO, "main", branch, transport=gh.transport())
    assert client is not None
    return client


def main_client(gh: FakeGitHub) -> GitHubClient:
    return GitHubClient("ghp_secret", REPO, transport=gh.transport())


# ----------------------------------------------------------------- the sandbox branch


async def test_a_lab_approval_opens_its_pull_request_against_the_sandbox_branch() -> None:
    gh = FakeGitHub()
    pr = await Approver(None, lab_client(gh)).pull_request(LAB, APPROVAL)
    assert pr.mode == "github" and pr.error is None
    assert pr.url == f"https://github.com/{REPO}/pull/9"
    (opened,) = gh.pull_requests()
    assert opened["base"] == "lab-remediations" and opened["head"] == "remediation/lab-1"
    assert opened["title"].startswith("[lab] ")
    assert "sandbox branch, never the main branch" in opened["body"]
    assert pr.path == "gitops/remediations/lab-04/networkpolicy-allow-trusted.yaml"
    assert pr.title.startswith("[lab] ")


async def test_the_remediation_branch_is_cut_from_the_sandbox_branch_not_from_main() -> None:
    gh = FakeGitHub()
    await Approver(None, lab_client(gh)).pull_request(LAB, APPROVAL)
    refs = [c for c in gh.calls if c[0] == "POST" and c[1] == f"{API}/git/refs"]
    assert refs == [("POST", f"{API}/git/refs",
                     {"ref": "refs/heads/remediation/lab-1", "sha": "labsha"})]
    assert not [c for c in gh.calls if c[1] == f"{API}/git/ref/heads/main"]


async def test_a_missing_sandbox_branch_is_created_from_the_head_of_main_first() -> None:
    gh = FakeGitHub(lab_branch_exists=False)
    pr = await Approver(None, lab_client(gh)).pull_request(LAB, APPROVAL)
    assert pr.mode == "github" and pr.error is None
    refs = [c[2] for c in gh.calls if c[0] == "POST" and c[1] == f"{API}/git/refs"]
    assert refs == [{"ref": "refs/heads/lab-remediations", "sha": "mainsha"},
                    {"ref": "refs/heads/remediation/lab-1", "sha": "labsha"}]
    assert gh.pull_requests()[0]["base"] == "lab-remediations"


async def test_main_is_only_ever_read_and_nothing_is_written_to_it() -> None:
    gh = FakeGitHub(lab_branch_exists=False)
    await Approver(None, lab_client(gh)).pull_request(LAB, APPROVAL)
    for _, path, payload in gh.writes():
        assert "heads/main" not in path
        assert (payload or {}).get("ref") != "refs/heads/main"
        assert (payload or {}).get("base") != "main"
        assert (payload or {}).get("branch") != "main"
    for call in gh.writes():
        if call[0] == "PUT":
            assert call[2]["branch"] == "remediation/lab-1"


async def test_a_lab_incident_never_uses_the_main_branch_client() -> None:
    main_gh = FakeGitHub()
    pr = await Approver(main_client(main_gh), None).pull_request(LAB, APPROVAL)
    assert pr.mode == "dry_run" and pr.url is None and pr.error is None
    assert main_gh.calls == [], "not one call: the main client is not even consulted"
    assert pr.title.startswith("[lab] ")


async def test_without_a_token_a_lab_approval_is_a_dry_run() -> None:
    assert build_lab_client("", REPO, "main", "lab-remediations") is None
    pr = await Approver(None, None).pull_request(LAB, APPROVAL)
    assert pr.mode == "dry_run" and pr.url is None and pr.content


async def test_a_real_incident_still_uses_the_main_client_and_never_the_lab_one() -> None:
    main_gh, lab_gh = FakeGitHub(), FakeGitHub()
    pr = await Approver(main_client(main_gh), lab_client(lab_gh)).pull_request(REAL, APPROVAL)
    assert pr.mode == "github" and pr.title.startswith("fix(") and "[lab]" not in pr.title
    assert main_gh.pull_requests()[0]["base"] == "main"
    assert lab_gh.calls == []
    assert "lab" not in main_gh.pull_requests()[0]["body"].lower().replace("label", "")


async def test_a_failed_lab_pull_request_is_an_error_on_the_approval_not_an_exception() -> None:
    gh = FakeGitHub()
    client = lab_client(gh)
    gh.handler = lambda request: httpx.Response(500, json={"message": "boom"})  # type: ignore[method-assign]
    client._transport = httpx.MockTransport(gh.handler)  # noqa: SLF001
    pr = await Approver(None, client).pull_request(LAB, APPROVAL)
    assert pr.mode == "github" and pr.url is None and "500" in (pr.error or "")


# --------------------------------------------------- the lab client refuses to be main


@pytest.mark.parametrize("branch", ["main", "master", "MAIN", "Main", "trunk", "develop",
                                    "production", "release", "-evil", "/x", "a..b", "x/",
                                    "x.lock", "a b", "a;b", "a\nb", "é", "a" * 101, "~x", "x^"])
def test_a_lab_branch_that_could_be_main_or_is_malformed_is_refused(branch: str) -> None:
    with pytest.raises(ValueError, match="not an acceptable lab branch"):
        build_lab_client("ghp_secret", REPO, "main", branch)


def test_the_configured_main_branch_is_refused_whatever_it_is_called() -> None:
    with pytest.raises(ValueError):
        build_lab_client("ghp_secret", REPO, "release-2026", "release-2026")
    assert build_lab_client("ghp_secret", REPO, "release-2026", "lab-remediations")


@pytest.mark.parametrize("branch", ["lab-remediations", "lab/remediations", "sandbox-1",
                                    "lab.fixes_2"])
def test_ordinary_sandbox_branch_names_are_accepted(branch: str) -> None:
    client = build_lab_client("ghp_secret", REPO, "main", branch)
    assert client is not None and client.base == branch and client.create_base_from == "main"


def test_the_default_sandbox_branch_is_lab_remediations() -> None:
    assert DEFAULT_LAB_BRANCH == "lab-remediations"
    client = build_lab_client("ghp_secret", REPO, "main", "")
    assert client is not None and client.base == "lab-remediations"


def test_the_main_client_does_not_create_branches_from_anything() -> None:
    assert GitHubClient("ghp_secret", REPO).create_base_from is None


async def test_a_missing_main_branch_is_still_an_error_for_the_main_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = GitHubClient("ghp_secret", REPO, transport=httpx.MockTransport(handler))
    pr = await Approver(client).pull_request(REAL, APPROVAL)
    assert pr.mode == "github" and "404" in (pr.error or "")


async def test_the_token_is_never_written_into_a_pull_request_or_a_file() -> None:
    gh = FakeGitHub(lab_branch_exists=False)
    pr = await Approver(None, lab_client(gh)).pull_request(LAB, APPROVAL)
    assert gh.calls
    for method, path, payload in gh.calls:
        assert "ghp_secret" not in json.dumps(payload or {}), (method, path)
        assert "ghp_secret" not in path
    assert "ghp_secret" not in pr.model_dump_json()


# ------------------------------------------------------------------- no learned memory


class RecordingLearner(Learner):
    def __init__(self) -> None:
        super().__init__(None, None, bucket="b", knowledge_base_id="k", data_source_id="d",
                         prefix="learned/")
        self.seen: list[str] = []

    async def learn(self, rec: IncidentRecord, approval: Approval) -> Learning:
        self.seen.append(rec.incident.incident_id)
        return Learning(status="written", key="learned/x.md", ingestion="started")


async def never(*args):  # type: ignore[no-untyped-def]
    raise AssertionError("not used")


def service(*records: IncidentRecord, learner: Learner | None,
            approver: Approver) -> IncidentService:
    svc = IncidentService(classify=never, delegate=never, learner=learner, approver=approver,
                          now=lambda: WHEN)
    for r in records:
        svc.store.put(r)
    return svc


async def test_an_approved_lab_incident_writes_no_postmortem_and_starts_no_ingestion() -> None:
    learner = RecordingLearner()
    svc = service(LAB, learner=learner, approver=Approver(None, None))
    done = await svc.decide("lab-1", "approved", "Judge")
    assert learner.seen == [], "the learner was never asked"
    assert done.status == "approved"
    learning = done.approval.learning
    assert learning is not None and learning.status == "disabled"
    assert learning.error == LAB_NOT_LEARNED == "lab incident"
    assert learning.key is None and learning.ingestion is None and learning.ingestion_job_id is None
    assert done.approval.pull_request is not None and done.approval.pull_request.mode == "dry_run"
    assert svc.store.get("lab-1").approval.learning == learning


async def test_the_lab_pull_request_still_opens_when_learning_is_skipped() -> None:
    gh = FakeGitHub()
    svc = service(LAB, learner=RecordingLearner(), approver=Approver(None, lab_client(gh)))
    done = await svc.decide("lab-1", "approved", "Judge", "tried it")
    assert done.approval.pull_request.url == f"https://github.com/{REPO}/pull/9"
    assert gh.pull_requests()[0]["base"] == "lab-remediations"
    assert done.approval.comment == "tried it"


async def test_a_real_incident_still_learns() -> None:
    learner = RecordingLearner()
    svc = service(REAL, learner=learner, approver=Approver(None, None))
    done = await svc.decide("real-1", "approved", "Roman")
    assert learner.seen == ["real-1"]
    assert done.approval.learning.status == "written"


@pytest.mark.parametrize("namespace", ["lab-00", "lab-15", "lab-1", "xlab-01", "labs", "agents"])
async def test_only_the_fourteen_lab_namespaces_count_as_lab(namespace: str) -> None:
    learner = RecordingLearner()
    other = record("o-1", namespace)
    svc = service(other, learner=learner, approver=Approver(None, None))
    await svc.decide("o-1", "approved", "Roman")
    assert learner.seen == ["o-1"]


async def test_a_rejected_lab_incident_changes_nothing_and_has_no_learning() -> None:
    gh = FakeGitHub()
    learner = RecordingLearner()
    svc = service(LAB, learner=learner, approver=Approver(None, lab_client(gh)))
    done = await svc.decide("lab-1", "rejected", "Judge")
    assert done.status == "rejected" and done.approval.pull_request is None
    assert done.approval.learning is None and learner.seen == [] and gh.calls == []


async def test_the_lab_skip_is_counted_apart_from_real_outcomes() -> None:
    from kiroween_orchestrator.metrics import LEARNED

    before = LEARNED.labels(result="skipped_lab")._value.get()  # noqa: SLF001
    svc = service(LAB, learner=RecordingLearner(), approver=Approver(None, None))
    await svc.decide("lab-1", "approved", "Judge")
    assert LEARNED.labels(result="skipped_lab")._value.get() == before + 1  # noqa: SLF001
