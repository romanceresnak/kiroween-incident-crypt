"""memory-and-ui TASK-05: approve/reject, patch rendering and the remediation PR."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from kiroween_orchestrator.api import create
from kiroween_orchestrator.approval import Approver, GitHubClient, render_patch, render_pr
from kiroween_orchestrator.service import IncidentService
from kiroween_shared.models import (
    Approval,
    Classification,
    Diagnosis,
    Evidence,
    Incident,
    IncidentRecord,
    Investigation,
    Proposal,
    SimilarIncident,
    Target,
    Usage,
)

WHEN = datetime(2026, 11, 17, 15, 0, tzinfo=UTC)
APPROVAL = Approval(decision="approved", by="romanceresnak", at=WHEN)


def record(iid: str, ns: str, kind: str, name: str, field: str, current: str | None,
           proposed: str, *, action: str = "set_field", container: str | None = None,
           reason: str = "Unhealthy", category: str = "network") -> IncidentRecord:
    incident = Incident.model_validate({
        "incident_id": iid, "source": "watcher", "reason": reason, "namespace": ns,
        "workload": {"kind": "Deployment", "name": "w"}, "observed_at": WHEN,
        "message": "Readiness probe failed"})
    proposal = Proposal(action=action, target=Target(namespace=ns, kind=kind, name=name,
                                                     container=container),
                        field=field, current=current, proposed=proposed,
                        rationale="because", risk="low")
    inv = Investigation(diagnosis=Diagnosis(root_cause="root", confidence="high", evidence=[
        Evidence(tool="get_x", field="f", value="v")]), proposal=proposal)
    return IncidentRecord(
        incident=incident, status="awaiting_approval", investigation=inv,
        classification=Classification(category=category, rationale="r"),
        similar_incidents=[SimilarIncident(title="PM-2026-058", score=0.8, fix="relabel")])


NETPOL = record("np-1", "chaos-netpol", "NetworkPolicy", "allow-trusted",
                "spec.ingress[0].from[0].podSelector.matchLabels.app", "netpol-trusted",
                "netpol-client")
BADIMAGE = record("bi-1", "chaos-badimage", "Deployment", "badimage-demo",
                  "spec.template.spec.containers[name=web].image",
                  "public.ecr.aws/docker/library/python:0.0.0-does-not-exist",
                  "public.ecr.aws/docker/library/python:3.12-slim", reason="ImagePullBackOff",
                  category="deploy_rollout")
PVC = record("pv-1", "chaos-pvc", "PersistentVolumeClaim", "data", "spec.storageClassName",
             "does-not-exist", "gp2", reason="ProvisioningFailed", category="storage")
OOM = record("oo-1", "chaos-oom", "Deployment", "oom-demo", "resources.limits.memory", "64Mi",
             "128Mi", action="adjust_resources", container="app", reason="OOMKilled",
             category="cpu_memory")
PORT = record("po-1", "shop", "Service", "web", "spec.ports[0].targetPort", "80", "8080")


def body(path_and_content: tuple[str, str]) -> dict:
    return yaml.safe_load(path_and_content[1])


# --- rendering (REQ-APR-03) ------------------------------------------------------------------


def test_netpol_change_is_a_json_patch_with_a_test() -> None:
    path, content = render_patch(NETPOL, APPROVAL)
    assert path == "gitops/remediations/chaos-netpol/networkpolicy-allow-trusted.yaml"
    doc = yaml.safe_load(content)
    assert doc["target"] == {"group": "networking.k8s.io", "version": "v1",
                             "kind": "NetworkPolicy", "name": "allow-trusted",
                             "namespace": "chaos-netpol"}
    ops = yaml.safe_load(doc["patch"])
    pointer = "/spec/ingress/0/from/0/podSelector/matchLabels/app"
    assert ops == [{"op": "test", "path": pointer, "value": "netpol-trusted"},
                   {"op": "replace", "path": pointer, "value": "netpol-client"}]
    assert "Approved by romanceresnak" in content


def test_badimage_rollback_is_a_strategic_merge_patch() -> None:
    doc = body(render_patch(BADIMAGE, APPROVAL))
    assert doc["apiVersion"] == "apps/v1" and doc["kind"] == "Deployment"
    assert doc["metadata"] == {"name": "badimage-demo", "namespace": "chaos-badimage"}
    assert doc["spec"]["template"]["spec"]["containers"] == [
        {"name": "web", "image": "public.ecr.aws/docker/library/python:3.12-slim"}]


def test_oom_resource_change_targets_the_container_in_the_pod_template() -> None:
    doc = body(render_patch(OOM, APPROVAL))
    assert doc["spec"]["template"]["spec"]["containers"] == [
        {"name": "app", "resources": {"limits": {"memory": "128Mi"}}}]


def test_pvc_change_and_its_immutability_warning() -> None:
    doc = body(render_patch(PVC, APPROVAL))
    assert (doc["apiVersion"], doc["spec"]) == ("v1", {"storageClassName": "gp2"})
    pr = render_pr(PVC, APPROVAL)
    assert "immutable" in pr.body and "recreated" in pr.body


def test_port_values_are_integers() -> None:
    ops = yaml.safe_load(body(render_patch(PORT, APPROVAL))["patch"])
    assert ops[1] == {"op": "replace", "path": "/spec/ports/0/targetPort", "value": 8080}
    assert ops[0]["value"] == 80


def test_absent_current_value_is_an_add() -> None:
    rec = record("ad-1", "shop", "Service", "web", "spec.ports[0].name", None, "http")
    ops = yaml.safe_load(body(render_patch(rec, APPROVAL))["patch"])
    assert ops == [{"op": "add", "path": "/spec/ports/0/name", "value": "http"}]


def test_pr_text() -> None:
    pr = render_pr(NETPOL, APPROVAL)
    assert pr.mode == "dry_run" and pr.branch == "remediation/np-1"
    assert pr.title == ("fix(chaos-netpol): spec.ingress[0].from[0].podSelector.matchLabels.app "
                        "on NetworkPolicy/allow-trusted (incident np-1)")
    for text in ["romanceresnak", "## Evidence", "`get_x` f: `v`", "PM-2026-058 (score 0.80)",
                 "`netpol-trusted` -> `netpol-client`", "read-only"]:
        assert text in pr.body, text


async def test_unrenderable_proposal_is_an_error_not_an_exception() -> None:
    rec = record("x-1", "shop", "Ingress", "web", "spec.rules[0].host", "a", "b")
    pr = await Approver().pull_request(rec, APPROVAL)
    assert pr.error and "Ingress is not supported" in pr.error


# --- GitHub (REQ-APR-04) ---------------------------------------------------------------------


class FakeGitHub:
    def __init__(self, *, branch_exists: bool = False, file_exists: bool = False,
                 fail_pr: bool = False) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.branch_exists, self.file_exists, self.fail_pr = branch_exists, file_exists, fail_pr
        self.auth: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.auth.add(request.headers.get("authorization", ""))
        payload = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, payload))
        repo = "/repos/romanceresnak/kiroween-incident-crypt"
        if request.method == "GET" and path == f"{repo}/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "base123"}})
        if request.method == "POST" and path == f"{repo}/git/refs":
            return httpx.Response(422 if self.branch_exists else 201,
                                  json={"message": "Reference already exists"})
        if request.method == "GET" and path.startswith(f"{repo}/contents/"):
            return (httpx.Response(200, json={"sha": "file456"}) if self.file_exists
                    else httpx.Response(404, json={"message": "Not Found"}))
        if request.method == "PUT" and path.startswith(f"{repo}/contents/"):
            return httpx.Response(201, json={})
        if request.method == "POST" and path == f"{repo}/pulls":
            if self.fail_pr:
                return httpx.Response(422, json={"message": "Validation Failed"})
            return httpx.Response(201, json={
                "html_url": "https://github.com/romanceresnak/kiroween-incident-crypt/pull/7"})
        return httpx.Response(500, json={"message": f"unexpected {request.method} {path}"})

    def client(self) -> GitHubClient:
        return GitHubClient("ghp_secret", "romanceresnak/kiroween-incident-crypt",
                            transport=httpx.MockTransport(self.handler))


async def test_github_branch_file_and_pr() -> None:
    gh = FakeGitHub()
    pr = await Approver(gh.client()).pull_request(NETPOL, APPROVAL)
    assert pr.mode == "github" and pr.error is None
    assert pr.url == "https://github.com/romanceresnak/kiroween-incident-crypt/pull/7"
    methods = [(m, p.rsplit("/", 1)[-1]) for m, p, _ in gh.calls]
    assert methods == [("GET", "main"), ("POST", "refs"),
                       ("GET", "networkpolicy-allow-trusted.yaml"),
                       ("PUT", "networkpolicy-allow-trusted.yaml"), ("POST", "pulls")]
    ref = gh.calls[1][2]
    assert ref == {"ref": "refs/heads/remediation/np-1", "sha": "base123"}
    put = gh.calls[3][2]
    assert put["branch"] == "remediation/np-1" and "sha" not in put
    assert base64.b64decode(put["content"]).decode() == pr.content
    assert gh.calls[4][2]["base"] == "main" and gh.calls[4][2]["head"] == "remediation/np-1"
    assert gh.auth == {"Bearer ghp_secret"}


async def test_existing_branch_and_file_are_updated() -> None:
    gh = FakeGitHub(branch_exists=True, file_exists=True)
    pr = await Approver(gh.client()).pull_request(NETPOL, APPROVAL)
    assert pr.url and gh.calls[3][2]["sha"] == "file456"


async def test_github_error_is_recorded_without_the_token() -> None:
    gh = FakeGitHub(fail_pr=True)
    pr = await Approver(gh.client()).pull_request(NETPOL, APPROVAL)
    assert pr.mode == "github" and pr.url is None
    assert "422 Validation Failed" in pr.error and "ghp_secret" not in pr.error


@pytest.mark.parametrize("repo", ["", "no-slash", "a/b/c", "../x"])
def test_github_client_validates_the_repository(repo: str) -> None:
    with pytest.raises(ValueError):
        GitHubClient("t", repo)


# --- service and API (REQ-APR-01, APR-02) ------------------------------------------------------


async def noop_classify(i: Incident):  # type: ignore[no-untyped-def]
    raise AssertionError("not used")


def service_with(*records: IncidentRecord, approver: Approver | None = None) -> IncidentService:
    svc = IncidentService(classify=noop_classify, delegate=noop_classify,  # type: ignore[arg-type]
                          approver=approver, now=lambda: WHEN)
    for r in records:
        svc.store.put(r)
    return svc


def post(client: TestClient, path: str, **kw):  # type: ignore[no-untyped-def]
    return client.post(path, json=kw.pop("json", {"by": "romanceresnak", "comment": "ok"}),
                       **kw)


def test_approve_records_who_when_and_a_dry_run_pr() -> None:
    svc = service_with(NETPOL)
    with TestClient(create(svc)) as client:
        response = post(client, "/incidents/np-1/approve")
        again = post(client, "/incidents/np-1/approve")
        fetched = client.get("/incidents/np-1").json()
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"
    assert data["approval"]["by"] == "romanceresnak" and data["approval"]["comment"] == "ok"
    assert data["approval"]["at"].startswith("2026-11-17T15:00")
    assert data["approval"]["pull_request"]["mode"] == "dry_run"
    assert fetched["approval"]["pull_request"]["path"].endswith("allow-trusted.yaml")
    assert again.status_code == 409


def test_reject_records_the_decision_and_renders_nothing() -> None:
    svc = service_with(PVC)
    with TestClient(create(svc)) as client:
        data = post(client, "/incidents/pv-1/reject").json()
    assert data["status"] == "rejected" and data["approval"]["pull_request"] is None


def test_only_awaiting_approval_can_be_decided() -> None:
    svc = service_with(NETPOL.model_copy(update={"status": "completed"}))
    with TestClient(create(svc)) as client:
        assert post(client, "/incidents/np-1/approve").status_code == 409
        assert post(client, "/incidents/missing/approve").status_code == 404


@pytest.mark.parametrize("payload", [{}, {"by": ""}, {"by": "<img>"}, {"by": "x" * 65},
                                     {"by": "a", "extra": 1}])
def test_invalid_approver_is_refused(payload: dict) -> None:
    svc = service_with(NETPOL)
    with TestClient(create(svc)) as client:
        assert post(client, "/incidents/np-1/approve", json=payload).status_code == 422
    assert svc.store.get("np-1").status == "awaiting_approval"


def test_non_json_request_is_refused() -> None:
    svc = service_with(NETPOL)
    with TestClient(create(svc)) as client:
        response = client.post("/incidents/np-1/approve", content='{"by": "x"}',
                               headers={"content-type": "text/plain"})
    assert response.status_code in (415, 422)
    assert svc.store.get("np-1").status == "awaiting_approval"


def test_cross_origin_request_is_refused_and_same_origin_allowed() -> None:
    svc = service_with(NETPOL, BADIMAGE)
    with TestClient(create(svc), base_url="http://localhost:8080") as client:
        evil = post(client, "/incidents/np-1/approve",
                    headers={"origin": "https://evil.example"})
        own = post(client, "/incidents/bi-1/approve",
                   headers={"origin": "http://localhost:8080"})
    assert evil.status_code == 403 and svc.store.get("np-1").status == "awaiting_approval"
    assert own.status_code == 200


async def test_github_mode_through_the_service() -> None:
    gh = FakeGitHub()
    svc = service_with(BADIMAGE, approver=Approver(gh.client()))
    rec = await svc.decide("bi-1", "approved", "romanceresnak")
    assert rec.approval.pull_request.url.endswith("/pull/7")
    assert svc.store.get("bi-1").approval.pull_request.mode == "github"


async def test_usage_is_untouched_by_a_decision() -> None:
    svc = service_with(OOM.model_copy(update={"usage_by_agent": {"orchestrator": Usage(
        input_tokens=5)}}))
    rec = await svc.decide("oo-1", "approved", "r")
    assert rec.usage_by_agent["orchestrator"].input_tokens == 5
