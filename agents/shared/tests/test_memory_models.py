"""memory-and-ui TASK-02: models for similar incidents and approvals."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from kiroween_shared.models import (
    Approval,
    Incident,
    IncidentRecord,
    PullRequest,
    SimilarIncident,
)

INCIDENT = {"incident_id": "m-1", "source": "watcher", "reason": "OOMKilled",
            "namespace": "chaos-oom", "workload": {"kind": "Deployment", "name": "oom-demo"},
            "observed_at": datetime(2026, 11, 17, tzinfo=UTC)}
HIT = {"title": "PM-2026-031", "category": "cpu_memory", "score": 0.71,
       "fix": "raise the limit", "source": "s3://b/postmortems/01.md"}


def test_defaults_keep_existing_producers_valid() -> None:
    incident = Incident.model_validate(INCIDENT)
    assert incident.similar_incidents == []
    record = IncidentRecord(incident=incident)
    assert record.similar_incidents == [] and record.approval is None


def test_incident_carries_at_most_five_similar_incidents() -> None:
    assert len(Incident.model_validate({**INCIDENT, "similar_incidents": [HIT] * 5})
               .similar_incidents) == 5
    with pytest.raises(ValidationError):
        Incident.model_validate({**INCIDENT, "similar_incidents": [HIT] * 6})


def test_similar_incident_limits() -> None:
    with pytest.raises(ValidationError):
        SimilarIncident.model_validate({**HIT, "fix": "x" * 1001})
    assert SimilarIncident.model_validate({"title": "t", "score": 0.1}).fix == ""


@pytest.mark.parametrize("by", ["romanceresnak", "Roman C.", "ops@example.com"])
def test_approver_names_accepted(by: str) -> None:
    assert Approval(decision="approved", by=by).at.tzinfo is not None


@pytest.mark.parametrize("by", ["", "x" * 65, "<script>", "a\nb"])
def test_approver_names_rejected(by: str) -> None:
    with pytest.raises(ValidationError):
        Approval(decision="approved", by=by)


def test_approval_with_a_dry_run_pull_request() -> None:
    pr = PullRequest(mode="dry_run", title="t", branch="remediation/m-1", path="p",
                     content="c", body="b")
    record = IncidentRecord(incident=Incident.model_validate(INCIDENT), status="approved",
                            approval=Approval(decision="approved", by="r", pull_request=pr))
    assert record.model_dump(mode="json")["approval"]["pull_request"]["url"] is None
