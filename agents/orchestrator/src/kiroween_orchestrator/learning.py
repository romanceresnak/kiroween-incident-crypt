"""Turn an approved incident into a postmortem the Knowledge Base can retrieve
(polish REQ-LRN-01 to LRN-07).

When a human approves an incident, the orchestrator writes ``learned/<id>.md`` and its
metadata sidecar to the postmortem bucket and starts an ingestion job. The next incident
that looks like this one then finds it as a similar incident.

The document records an *approved proposal*. Nobody has checked that the pull request was
merged or that the fix worked, and the text says so.

Everything inserted into the document came from the cluster or from a model, and the
specialists later read it back as a hint. So code builds it from bounded single-line fields:
no inserted value can start a new heading or section.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

from botocore.exceptions import ClientError

from kiroween_shared.models import Approval, IncidentRecord, Learning

from .approval import full_path

logger = logging.getLogger(__name__)

TIMEOUT_S = 20.0
DEFAULT_PREFIX = "learned/"
MAX_EVIDENCE = 8
CHANGE_ACTIONS = frozenset({"adjust_resources", "set_field"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def clean(value: object, limit: int) -> str:
    """One line, no control characters, at most ``limit`` characters."""
    text = _CONTROL.sub(" ", "" if value is None else str(value))
    return " ".join(text.split())[:limit]


def can_learn(record: IncidentRecord) -> bool:
    """Only an incident with a change proposal has something to learn from (REQ-LRN-04)."""
    inv = record.investigation
    return bool(inv and inv.proposal and inv.proposal.target is not None
                and inv.proposal.action in CHANGE_ACTIONS)


def render_postmortem(record: IncidentRecord, approval: Approval) -> tuple[str, dict[str, Any]]:
    """The postmortem markdown and its metadata sidecar (REQ-LRN-02, LRN-03)."""
    incident, inv = record.incident, record.investigation
    if inv is None or inv.proposal is None or inv.proposal.target is None or not can_learn(record):
        raise ValueError("the incident has no change proposal")
    proposal, target = inv.proposal, inv.proposal.target
    # The object that changes is the proposal's target (a NetworkPolicy, a PVC, ...), which
    # is often not the incident's workload.
    changed = f"{clean(target.kind, 63)} `{clean(target.name, 253)}`"
    changed_plain = f"{clean(target.kind, 63)}/{clean(target.name, 253)}"
    category = (record.classification.category if record.classification else "general")
    if category == "unknown":
        category = "general"

    reason = clean(incident.reason, 64)
    kind, name = clean(incident.workload.kind, 63), clean(incident.workload.name, 253)
    title = clean(f"LRN-{incident.incident_id}: {reason} on {kind} {name}", 200)
    path = clean(full_path(proposal), 200)
    current, proposed = clean(proposal.current, 200), clean(proposal.proposed, 200)
    when = approval.at.date().isoformat()
    by = clean(approval.by, 64)
    root_cause = clean(inv.diagnosis.root_cause, 600)
    pr = approval.pull_request
    delivered = (f"pull request {clean(pr.url, 200)}" if pr and pr.url
                 else "a dry-run pull request (nothing was opened)")
    where = clean(f"{incident.namespace} / {kind} {name}"
                  + (f", container {incident.container}" if incident.container else ""), 300)

    evidence = [
        f"- `{clean(e.tool, 60)}` {clean(e.field, 80)}: {clean(e.value, 200)}"
        for e in inv.diagnosis.evidence[:MAX_EVIDENCE]
    ] or ["- none recorded"]

    markdown = "\n".join([
        f"# {title}",
        "",
        f"- **Date:** {when}",
        "- **Source:** learned from an approved incident, not a reviewed postmortem",
        f"- **Signal:** {reason}",
        f"- **Namespace / workload:** {where}",
        "",
        "## Summary",
        "",
        f"{root_cause} The incident ({reason}) was approved by {by} on {when}. "
        f"Message seen: {clean(incident.message, 300) or '(none)'}",
        "",
        "## Impact",
        "",
        "Not recorded. This postmortem was written automatically when the incident was "
        "approved; no impact was measured.",
        "",
        "## Root cause",
        "",
        f"{root_cause} (diagnosis confidence: {clean(inv.diagnosis.confidence, 10)}).",
        "",
        "## Evidence",
        "",
        *evidence,
        "",
        "## Fix",
        "",
        f"Approved change, not yet confirmed to work: set `{path}` on {changed} from "
        f"`{current}` to `{proposed}`. Rationale: {clean(proposal.rationale, 400) or '(none)'} "
        f"Risk: {clean(proposal.risk, 300) or '(none)'} Delivered as {delivered}.",
        "",
        "## Prevention",
        "",
        "Not recorded. Check that the approved change was merged and fixed the problem "
        "before relying on this postmortem.",
        "",
    ])
    sidecar = {"metadataAttributes": {
        "title": title,
        "category": category,
        "signal": reason,
        "fix": clean(f"Approved fix (not yet confirmed): set {path} on {changed_plain} from "
                     f"{current} to {proposed}.", 600),
        "source": "learned",
    }}
    return markdown, sidecar


def error_name(exc: BaseException) -> str:
    """The error class or AWS error code, never the message (it can echo request data)."""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        if code:
            return clean(code, 100)
    return clean(type(exc).__name__, 100)


class Learner:
    """Writes the postmortem and starts ingestion. ``learn`` never raises."""

    def __init__(self, s3: Any, agent: Any, *, bucket: str, knowledge_base_id: str,
                 data_source_id: str, prefix: str = DEFAULT_PREFIX,
                 timeout_s: float = TIMEOUT_S) -> None:
        self._s3, self._agent = s3, agent
        self.bucket, self.knowledge_base_id = bucket, knowledge_base_id
        self.data_source_id = data_source_id
        self.prefix = prefix if prefix.endswith("/") else prefix + "/"
        self.timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self.bucket and self.knowledge_base_id and self.data_source_id)

    async def learn(self, record: IncidentRecord, approval: Approval) -> Learning:
        if not self.enabled:
            return Learning(status="disabled")
        try:
            async with asyncio.timeout(self.timeout_s):
                return await asyncio.to_thread(self._learn, record, approval)
        except TimeoutError:
            logger.error("learning timed out after %g s", self.timeout_s)
            return Learning(status="error", error=f"timed out after {self.timeout_s:g} s")
        except Exception as exc:
            logger.error("learning failed: %s", error_name(exc))
            return Learning(status="error", error=error_name(exc))

    def _learn(self, record: IncidentRecord, approval: Approval) -> Learning:
        markdown, sidecar = render_postmortem(record, approval)
        key = f"{self.prefix}{record.incident.incident_id}.md"
        # Sidecar first: a job that starts between the two writes must not index a document
        # without its metadata.
        self._s3.put_object(Bucket=self.bucket, Key=f"{key}.metadata.json",
                            Body=json.dumps(sidecar, indent=2).encode(),
                            ContentType="application/json")
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=markdown.encode(),
                            ContentType="text/markdown; charset=utf-8")
        ingestion, job_id, error = self._start_ingestion(record.incident.incident_id)
        logger.info("learned %s (ingestion %s)", key, ingestion)
        return Learning(status="written", key=key, ingestion=ingestion,
                        ingestion_job_id=job_id, error=error)

    def _start_ingestion(self, incident_id: str) -> tuple[str, str | None, str | None]:
        try:
            response = self._agent.start_ingestion_job(
                knowledgeBaseId=self.knowledge_base_id, dataSourceId=self.data_source_id,
                # The same incident always gets the same token, so a repeat is not a new job.
                clientToken=str(uuid.uuid5(uuid.NAMESPACE_URL, f"kiroween-learned:{incident_id}")),
                description=clean(f"learned {incident_id}", 200),
            )
        except ClientError as exc:
            code = error_name(exc)
            if code == "ConflictException":
                # A job is running. The document is written and the next job indexes it.
                return "already_running", None, None
            return "failed", None, code
        except Exception as exc:  # network error, timeout: the documents are written
            return "failed", None, error_name(exc)
        return "started", clean(response["ingestionJob"]["ingestionJobId"], 100), None


def build_learner(env: dict[str, str] | Any) -> Learner:
    """From the environment. Without bucket, KB ID and data source ID, learning is disabled
    and no AWS client is created."""
    bucket = env.get("LEARNING_BUCKET", "")
    kb_id = env.get("KNOWLEDGE_BASE_ID", "")
    data_source_id = env.get("KNOWLEDGE_BASE_DATA_SOURCE_ID", "")
    prefix = env.get("LEARNING_PREFIX", DEFAULT_PREFIX) or DEFAULT_PREFIX
    if not (bucket and kb_id and data_source_id):
        return Learner(None, None, bucket=bucket, knowledge_base_id=kb_id,
                       data_source_id=data_source_id, prefix=prefix)
    import boto3
    from botocore.config import Config

    config = Config(retries={"max_attempts": 2, "mode": "standard"}, connect_timeout=5,
                    read_timeout=10)
    region = env.get("AWS_REGION", "eu-west-1")
    return Learner(boto3.client("s3", region_name=region, config=config),
                   boto3.client("bedrock-agent", region_name=region, config=config),
                   bucket=bucket, knowledge_base_id=kb_id, data_source_id=data_source_id,
                   prefix=prefix)
