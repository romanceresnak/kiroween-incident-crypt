"""Turn an approved proposal into a /gitops change (memory-and-ui REQ-APR-03, APR-04).

The agents never change the cluster. An approved proposal becomes a file under
``gitops/remediations/`` and a pull request; Argo CD applies it after a human merges it.
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

import httpx
import yaml

from kiroween_shared.models import Approval, IncidentRecord, Proposal, PullRequest
from kiroween_shared.proposals import PathError, parse_path

from .auth import is_lab_namespace

logger = logging.getLogger(__name__)

API_VERSIONS = {
    "Deployment": "apps/v1", "StatefulSet": "apps/v1", "DaemonSet": "apps/v1",
    "Service": "v1", "PersistentVolumeClaim": "v1", "ConfigMap": "v1",
    "NetworkPolicy": "networking.k8s.io/v1",
}
POD_TEMPLATE_KINDS = frozenset({"Deployment", "StatefulSet", "DaemonSet"})
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# A branch a lab pull request may target: plain, never starting with "-" or "/", no "..".
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
DEFAULT_LAB_BRANCH = "lab-remediations"
PROTECTED_BRANCHES = frozenset({"main", "master", "trunk", "develop", "production", "release"})


class RenderError(ValueError):
    pass


def full_path(proposal: Proposal) -> str:
    """The field path from the object root. adjust_resources names a container field."""
    field = proposal.field or ""
    target = proposal.target
    if (proposal.action == "adjust_resources" and target is not None
            and target.kind in POD_TEMPLATE_KINDS and target.container):
        return f"spec.template.spec.containers[name={target.container}].{field}"
    return field


def _value(field: str, value: str | None) -> Any:
    # Ports are integers in the API; everything else the specialists propose is a string.
    if value is not None and re.fullmatch(r"\d+", value) and "port" in field.lower():
        return int(value)
    return value


def _pointer(tokens: list[Any]) -> str:
    parts = []
    for token in tokens:
        if isinstance(token, tuple):
            raise RenderError("a JSON patch cannot address a list item by name")
        parts.append(str(token).replace("~", "~0").replace("/", "~1"))
    return "/" + "/".join(parts)


def _strategic(tokens: list[Any], value: Any) -> dict[str, Any]:
    """Nested mapping for a path of map keys and [name=...] selectors."""
    node: Any = value
    for token in reversed(tokens):
        node = [{"name": token[1], **node}] if isinstance(token, tuple) else {token: node}
    return node


def render_patch(record: IncidentRecord, approval: Approval) -> tuple[str, str]:
    """(repository path, file content) for the approved proposal."""
    inv = record.investigation
    proposal = inv.proposal if inv else None
    if proposal is None or proposal.target is None or proposal.action not in (
            "adjust_resources", "set_field"):
        raise RenderError("the incident has no change proposal to render")
    target = proposal.target
    api_version = API_VERSIONS.get(target.kind)
    if api_version is None:
        raise RenderError(f"kind {target.kind} is not supported")
    path = full_path(proposal)
    try:
        tokens = parse_path(path)
    except PathError as exc:
        raise RenderError(str(exc)) from exc
    if isinstance(tokens[-1], tuple):
        raise RenderError("the field path must end in a field, not a list item")
    proposed = _value(path, proposal.proposed)
    current = _value(path, proposal.current)
    header = (
        f"# Approved remediation for incident {record.incident.incident_id} "
        f"({record.incident.reason}).\n"
        f"# Approved by {approval.by} at {approval.at.isoformat()}.\n"
        f"# {target.kind}/{target.name} {path}: {proposal.current!r} -> {proposal.proposed!r}\n"
    )
    metadata = {"name": target.name, "namespace": target.namespace}
    if any(isinstance(t, int) for t in tokens):
        # List positions: RFC 6902, with a test so the patch fails if the value moved.
        group, _, version = api_version.rpartition("/")
        ops: list[dict[str, Any]] = []
        pointer = _pointer(tokens)
        if current is None:
            ops.append({"op": "add", "path": pointer, "value": proposed})
        else:
            ops += [{"op": "test", "path": pointer, "value": current},
                    {"op": "replace", "path": pointer, "value": proposed}]
        target_ref = {**({"group": group} if group else {}), "version": version,
                      "kind": target.kind, **metadata}
        document = {"target": target_ref,
                    "patch": yaml.safe_dump(ops, sort_keys=False)}
        kind = "# Kustomize patch entry (RFC 6902 JSON patch).\n"
    else:
        document = {"apiVersion": api_version, "kind": target.kind, "metadata": metadata,
                    **_strategic(tokens, proposed)}
        kind = "# Kustomize strategic merge patch.\n"
    file_path = (f"gitops/remediations/{target.namespace}/"
                 f"{target.kind.lower()}-{target.name}.yaml")
    return file_path, header + kind + yaml.safe_dump(document, sort_keys=False)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- none"


def render_pr(record: IncidentRecord, approval: Approval) -> PullRequest:
    path, content = render_patch(record, approval)
    incident = record.incident
    inv = record.investigation
    if inv is None or inv.proposal is None or inv.proposal.target is None:
        raise RenderError("the incident has no change proposal to render")
    proposal, target = inv.proposal, inv.proposal.target
    title = (f"fix({target.namespace}): {full_path(proposal)} on {target.kind}/{target.name} "
             f"(incident {incident.incident_id})")[:250]
    warnings = []
    if target.kind == "PersistentVolumeClaim" and proposal.field == "spec.storageClassName":
        warnings.append("`spec.storageClassName` is immutable: the claim must be deleted and "
                        "recreated. Check that it never bound, or back up its data first.")
    category = record.classification.category if record.classification else "unknown"
    evidence = [f"`{e.tool}` {e.field}: `{e.value}`" for e in inv.diagnosis.evidence]
    similar = [f"{s.title} (score {s.score:.2f}): {s.fix}" for s in record.similar_incidents]
    body = "\n\n".join([
        f"Approved remediation for incident `{incident.incident_id}`, approved by "
        f"**{approval.by}** at {approval.at.isoformat()}.",
        "## Incident\n" + _bullets([
            f"Signal: `{incident.reason}` on {incident.workload.kind} "
            f"`{incident.namespace}/{incident.workload.name}`",
            f"Category: `{category}`",
            f"Message: {incident.message[:300] or '(none)'}"]),
        f"## Diagnosis\n{inv.diagnosis.root_cause}\n\nConfidence: {inv.diagnosis.confidence}",
        "## Evidence\n" + _bullets(evidence),
        "## Change\n" + _bullets([
            f"`{target.kind}/{target.name}` in `{target.namespace}`",
            f"`{full_path(proposal)}`: `{proposal.current}` -> `{proposal.proposed}`",
            f"File: `{path}`"]),
        f"## Rationale\n{proposal.rationale or '(none)'}\n\n## Risk\n{proposal.risk or '(none)'}",
        *(["## Warning\n" + _bullets(warnings)] if warnings else []),
        "## Similar past incidents\n" + _bullets(similar),
        ("Generated by the Kiroween orchestrator. The agents have read-only access; this "
         "change takes effect only after this PR is reviewed and merged."
         + (f"\n\nApprover comment: {approval.comment}" if approval.comment else "")),
    ])
    return PullRequest(mode="dry_run", title=title, branch=f"remediation/{incident.incident_id}",
                       path=path, content=content, body=body)


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    """Opens the remediation PR. The token is held here and never logged or returned."""

    def __init__(self, token: str, repository: str, *, base: str = "main",
                 api: str = "https://api.github.com",
                 transport: httpx.AsyncBaseTransport | None = None,
                 create_base_from: str | None = None) -> None:
        if not token:
            raise ValueError("a GitHub token is required")
        if not _REPO.match(repository) or {".", ".."} & set(repository.split("/")):
            raise ValueError(f"invalid repository {repository!r}")
        self._headers = {"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"}
        self.repository = repository
        self.base = base
        # The branch to create ``base`` from when it does not exist yet (the lab sandbox).
        self.create_base_from = create_base_from
        self.api = api.rstrip("/")
        self._transport = transport

    async def open_pr(self, pr: PullRequest) -> str:
        repo = f"{self.api}/repos/{self.repository}"
        async with httpx.AsyncClient(headers=self._headers, timeout=20,
                                     transport=self._transport) as gh:
            async def call(method: str, url: str, ok: tuple[int, ...] = (200, 201),
                           **kw: Any) -> httpx.Response:
                response = await gh.request(method, url, **kw)
                if response.status_code not in ok:
                    message = ""
                    try:
                        message = str(response.json().get("message", ""))[:200]
                    except ValueError:
                        pass
                    raise GitHubError(f"{method} {url.removeprefix(self.api)} returned "
                                      f"{response.status_code} {message}".strip())
                return response

            ok_base = (200, 404) if self.create_base_from else (200,)
            base = await call("GET", f"{repo}/git/ref/heads/{self.base}", ok=ok_base)
            if base.status_code == 404:
                # The sandbox branch is created once, from the main branch's head.
                source = await call("GET", f"{repo}/git/ref/heads/{self.create_base_from}")
                await call("POST", f"{repo}/git/refs", ok=(201, 422), json={
                    "ref": f"refs/heads/{self.base}", "sha": source.json()["object"]["sha"]})
                base = await call("GET", f"{repo}/git/ref/heads/{self.base}")
            sha = base.json()["object"]["sha"]
            # 422: the branch already exists (an earlier attempt); reuse it.
            await call("POST", f"{repo}/git/refs", ok=(201, 422),
                       json={"ref": f"refs/heads/{pr.branch}", "sha": sha})
            existing = await call("GET", f"{repo}/contents/{pr.path}", ok=(200, 404),
                                  params={"ref": pr.branch})
            put: dict[str, Any] = {
                "message": pr.title, "branch": pr.branch,
                "content": base64.b64encode(pr.content.encode()).decode()}
            if existing.status_code == 200:
                put["sha"] = existing.json()["sha"]
            await call("PUT", f"{repo}/contents/{pr.path}", json=put)
            created = await call("POST", f"{repo}/pulls", json={
                "title": pr.title, "head": pr.branch, "base": self.base, "body": pr.body})
            return str(created.json()["html_url"])


def build_lab_client(token: str, repository: str, main_base: str, branch: str,
                     transport: httpx.AsyncBaseTransport | None = None) -> GitHubClient | None:
    """The client lab approvals use: pull requests against a sandbox branch, never the main
    one (scenario-lab REQ-SB-01, SB-02). Raises ValueError if the branch could be main."""
    branch = branch or DEFAULT_LAB_BRANCH
    if (branch.lower() in PROTECTED_BRANCHES or branch == main_base or not _BRANCH.match(branch)
            or ".." in branch or branch.endswith(("/", ".lock"))):
        raise ValueError(f"{branch!r} is not an acceptable lab branch")
    if not token:
        return None
    return GitHubClient(token, repository, base=branch, transport=transport,
                        create_base_from=main_base)


class Approver:
    """Renders the PR and, with a GitHub client, opens it (REQ-APR-04).

    A lab incident (namespace lab-01 to lab-14) only ever uses ``lab_github``, and without one
    it is a dry run. It never falls back to the main-branch client (scenario-lab REQ-SB-03).
    """

    def __init__(self, github: GitHubClient | None = None,
                 lab_github: GitHubClient | None = None) -> None:
        self.github = github
        self.lab_github = lab_github

    async def pull_request(self, record: IncidentRecord, approval: Approval) -> PullRequest:
        lab = is_lab_namespace(record.incident.namespace)
        client = self.lab_github if lab else self.github
        try:
            pr = render_pr(record, approval)
        except RenderError as exc:
            return PullRequest(mode="dry_run", title="", branch="", path="", content="",
                               body="", error=f"could not render the change: {exc}")
        if lab:
            pr = pr.model_copy(update={
                "title": f"[lab] {pr.title}",
                "body": ("**Lab incident.** This pull request targets a sandbox branch, never "
                         "the main branch, and nothing in it is applied to the cluster.\n\n"
                         + pr.body)})
        if client is None:
            return pr
        try:
            url = await client.open_pr(pr)
        except Exception as exc:
            logger.error("opening the remediation PR failed: %s", exc)
            return pr.model_copy(update={"mode": "github", "error": str(exc)[:300]})
        logger.info("opened remediation PR %s", url)
        return pr.model_copy(update={"mode": "github", "url": url})
