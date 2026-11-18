"""Data exchanged between the watcher, the orchestrator and the specialists."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Kubernetes name rules: DNS-1123 label (namespaces, containers) and subdomain (pods).
DNS_LABEL = r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$"
DNS_SUBDOMAIN = r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$"

Category = Literal["cpu_memory", "network", "deploy_rollout", "storage", "unknown"]
Confidence = Literal["low", "medium", "high"]
Action = Literal["adjust_resources", "set_field", "investigate_further", "none"]
Status = Literal[
    "accepted", "processing", "awaiting_approval", "no_specialist", "completed", "failed",
    "correlated", "approved", "rejected",
]
# action_required: broken now. transient: it was real but the subject is healthy now.
# no_action: nothing is wrong. Only action_required carries a proposal (hardening REQ-OUT).
Outcome = Literal["action_required", "transient", "no_action"]

DELIVERY = (
    "Apply as a pull request against /gitops after a human approves it. "
    "The agent does not change the cluster."
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Workload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern=r"^[A-Z][A-Za-z]{0,62}$")
    name: str = Field(pattern=DNS_SUBDOMAIN)


class SimilarIncident(BaseModel):
    """A past postmortem found by incident-memory (memory-and-ui REQ-MEM-03)."""

    title: str = Field(max_length=200)
    category: str | None = Field(default=None, max_length=40)
    score: float
    fix: str = Field(default="", max_length=1000)
    source: str = Field(default="", max_length=300)
    excerpt: str = Field(default="", max_length=1000)
    # Where the postmortem came from (polish REQ-LRN-08): written by hand and seeded from
    # docs/postmortems, or learned from an approved incident.
    origin: Literal["seeded", "learned"] = "seeded"


class Incident(BaseModel):
    """An incident as submitted to ``POST /incidents`` (REQ-ORC-02)."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    source: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=64)
    namespace: str = Field(pattern=DNS_LABEL)
    workload: Workload
    # Optional: an incident about a PersistentVolumeClaim has no pod or container.
    pod: str | None = Field(default=None, pattern=DNS_SUBDOMAIN)
    container: str | None = Field(default=None, pattern=DNS_LABEL)
    message: str = Field(default="", max_length=2000)
    observed_at: datetime
    # Objects the pod needs, e.g. the PVCs it mounts; used for correlation (REQ-WSU-06).
    depends_on: list[Workload] = Field(default_factory=list, max_length=20)
    # Past postmortems that look alike; hints for the specialist, not evidence (REQ-MEM-07).
    similar_incidents: list[SimilarIncident] = Field(default_factory=list, max_length=5)


class Classification(BaseModel):
    """Orchestrator classification of an incident (REQ-ORC-06)."""

    category: Category
    rationale: str = Field(description="One sentence explaining the category.", max_length=400)
    confidence: Confidence = Field(
        default="medium",
        description="How sure the category is. Use low when the incident could fit more than one.",
    )


class Evidence(BaseModel):
    tool: str = Field(description="Name of the tool the value came from.")
    field: str = Field(description="Field or line in that tool result.")
    value: str = Field(description="The value as observed.")


class Diagnosis(BaseModel):
    root_cause: str
    confidence: Confidence
    evidence: list[Evidence] = Field(default_factory=list)


class Target(BaseModel):
    namespace: str
    kind: str
    name: str
    container: str | None = None


class ProposalDraft(BaseModel):
    """What the specialist model may propose. Code turns it into a ``Proposal``."""

    action: Action
    target: Target | None = None
    field: str | None = Field(
        default=None,
        description=(
            "adjust_resources: one of resources.limits.memory, resources.requests.memory, "
            "resources.limits.cpu, resources.requests.cpu. set_field: the field path your "
            "instructions allow, for example spec.selector.app."
        ),
    )
    current: str | None = Field(default=None, description="Current value exactly as read.")
    proposed: str | None = None
    rationale: str = ""
    risk: str = ""


class Proposal(ProposalDraft):
    """A validated proposal. It always requires human approval (REQ-CPU-05, CPU-07)."""

    requires_approval: Literal[True] = True
    delivery: Literal[DELIVERY] = DELIVERY  # type: ignore[valid-type]


OUTCOME_HELP = (
    "action_required: the subject is broken now and needs a change or a human. "
    "transient: the incident was real but the subject is healthy now (for example a "
    "readiness failure while the container started). no_action: nothing is wrong. "
    "Only action_required has a proposal; for the other two leave proposal null."
)


class SpecialistOutput(BaseModel):
    """Structured output requested from a specialist model."""

    outcome: Outcome = Field(default="action_required", description=OUTCOME_HELP)
    diagnosis: Diagnosis
    proposal: ProposalDraft | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None

    def __add__(self, other: Usage) -> Usage:
        if self.cost_usd is None and other.cost_usd is None:
            cost = None
        else:
            cost = (self.cost_usd or 0.0) + (other.cost_usd or 0.0)
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=cost,
        )


class Investigation(BaseModel):
    """A specialist's response to ``POST /investigate`` (REQ-CPU-04, hardening REQ-OUT)."""

    outcome: Outcome = "action_required"
    diagnosis: Diagnosis
    proposal: Proposal | None = None
    usage: Usage = Field(default_factory=Usage)
    validation_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _proposal_matches_outcome(self) -> Investigation:
        if self.outcome == "action_required" and self.proposal is None:
            raise ValueError("outcome action_required needs a proposal")
        if self.outcome != "action_required" and self.proposal is not None:
            raise ValueError(f"outcome {self.outcome} must not have a proposal")
        return self


class RelatedIncident(BaseModel):
    """An incident attached to a primary instead of being investigated (REQ-COR-03)."""

    incident_id: str
    reason: str
    workload: Workload
    pod: str | None = None
    match: Literal["same_workload", "depends_on", "shared_dependency"]
    observed_at: datetime


class PullRequest(BaseModel):
    """The rendered /gitops change for an approved proposal (REQ-APR-03, APR-04)."""

    mode: Literal["dry_run", "github"]
    title: str
    branch: str
    path: str
    content: str
    body: str
    url: str | None = None
    error: str | None = None


class Approval(BaseModel):
    """A human decision on a proposal (REQ-APR-01)."""

    decision: Literal["approved", "rejected"]
    by: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9 ._@-]+$")
    at: datetime = Field(default_factory=utcnow)
    comment: str = Field(default="", max_length=500)
    pull_request: PullRequest | None = None


class IncidentRecord(BaseModel):
    """The orchestrator's record of one incident (REQ-ORC-10)."""

    incident: Incident
    status: Status = "accepted"
    classification: Classification | None = None
    investigation: Investigation | None = None
    delegation: Literal["tool", "forced"] | None = None
    usage_by_agent: dict[str, Usage] = Field(default_factory=dict)
    error: str | None = None
    related_incidents: list[RelatedIncident] = Field(default_factory=list)
    related_to: str | None = None
    similar_incidents: list[SimilarIncident] = Field(default_factory=list)
    approval: Approval | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
