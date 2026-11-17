"""hardening TASK-01: outcome invariant and the shared outcome decision."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from kiroween_shared.config import Settings
from kiroween_shared.k8s.client import KubeError
from kiroween_shared.k8s.extra_tools import network_tools
from kiroween_shared.metrics import PriceTable
from kiroween_shared.models import (
    Diagnosis,
    Evidence,
    Incident,
    IncidentRecord,
    Investigation,
    Proposal,
    Target,
)
from kiroween_shared.outcome import (
    STATE_SIGNALS,
    contradicting_evidence,
    decide,
    subject_health,
)
from kiroween_shared.proposals import LABEL_KEY_SEGMENT, ChangeRule, label_value
from kiroween_shared.specialist import SpecialistSpec, run_investigation
from kiroween_shared.testing import FakeCluster, ScriptedModel, obj, tool_call

MODEL = "eu.anthropic.claude-sonnet-4-6"
WHEN = datetime(2026, 11, 17, tzinfo=UTC)


def incident(reason: str = "Unhealthy", *, kind: str = "Deployment", name: str = "web",
             pod: str | None = "web-1", container: str | None = "web") -> Incident:
    return Incident.model_validate({
        "incident_id": "o-1", "source": "watcher", "reason": reason, "namespace": "shop",
        "workload": {"kind": kind, "name": name}, "pod": pod, "container": container,
        "observed_at": WHEN})


def pod(name: str = "web-1", *, ready: bool, container_ready: bool | None = None) -> dict:
    status = "True" if ready else "False"
    return obj("Pod", name, "shop", status={
        "conditions": [{"type": "Ready", "status": status}],
        "containerStatuses": [{"name": "web", "ready": ready if container_ready is None
                               else container_ready}]})


def deployment(**status: int) -> dict:
    return obj("Deployment", "web", "shop", metadata={"generation": 2},
               spec={"replicas": 1}, status={"observedGeneration": 2, **status})


FIX = Proposal(action="set_field", target=Target(namespace="shop", kind="Service", name="web"),
               field="spec.selector.app", current="wbe", proposed="web")
FURTHER = Proposal(action="investigate_further")


def diag(*evidence: tuple[str, str, str]) -> Diagnosis:
    return Diagnosis(root_cause="r", confidence="high",
                     evidence=[Evidence(tool=t, field=f, value=v) for t, f, v in evidence])


# --- model invariant (REQ-OUT-02) --------------------------------------------------------


def test_action_required_needs_a_proposal() -> None:
    with pytest.raises(ValidationError, match="needs a proposal"):
        Investigation(diagnosis=diag())


@pytest.mark.parametrize("outcome", ["transient", "no_action"])
def test_other_outcomes_must_not_have_a_proposal(outcome: str) -> None:
    with pytest.raises(ValidationError, match="must not have a proposal"):
        Investigation(outcome=outcome, diagnosis=diag(), proposal=FIX)
    assert Investigation(outcome=outcome, diagnosis=diag()).proposal is None


def test_existing_producers_default_to_action_required() -> None:
    assert Investigation(diagnosis=diag(), proposal=FIX).outcome == "action_required"


def test_record_has_correlation_fields_and_incident_depends_on() -> None:
    record = IncidentRecord(incident=incident())
    assert record.related_incidents == [] and record.related_to is None
    assert record.incident.depends_on == []


# --- live health (REQ-OUT-03) ---------------------------------------------------------------


def test_ready_pod_is_healthy() -> None:
    assert subject_health(incident(), FakeCluster([pod(ready=True)])) == "pod web-1 is Ready"


def test_not_ready_pod_is_not_healthy() -> None:
    assert subject_health(incident(), FakeCluster([pod(ready=False)])) is None


def test_ready_pod_with_the_incident_container_not_ready_is_not_healthy() -> None:
    kube = FakeCluster([pod(ready=True, container_ready=False)])
    assert subject_health(incident(), kube) is None


def test_bound_pvc_is_healthy_and_pending_is_not() -> None:
    inc = incident("ProvisioningFailed", kind="PersistentVolumeClaim", name="data", pod=None,
                   container=None)
    bound = FakeCluster([obj("PersistentVolumeClaim", "data", "shop",
                             status={"phase": "Bound"})])
    pending = FakeCluster([obj("PersistentVolumeClaim", "data", "shop",
                               status={"phase": "Pending"})])
    assert subject_health(inc, bound) == "PVC data is Bound"
    assert subject_health(inc, pending) is None


def test_gone_pod_falls_back_to_the_deployment() -> None:
    healthy = deployment(updatedReplicas=1, readyReplicas=1, availableReplicas=1)
    assert "all replicas" in subject_health(incident(), FakeCluster([healthy]))


def test_rollout_with_an_old_pod_still_serving_is_not_healthy() -> None:
    # badimage: the old pod keeps the Deployment available, the new one cannot start.
    stuck = deployment(updatedReplicas=1, readyReplicas=1, availableReplicas=1,
                       unavailableReplicas=1)
    assert subject_health(incident(), FakeCluster([stuck])) is None


def test_read_errors_are_not_healthy() -> None:
    kube = FakeCluster([pod(ready=True)], errors={"get_pod": KubeError(403, "forbidden")})
    assert subject_health(incident(), kube) is None


# --- evidence contradiction (REQ-OUT-04) -------------------------------------------------


def test_evidence_naming_the_incident_pod_ready_true_contradicts() -> None:
    entry = contradicting_evidence(incident(), diag(
        ("list_endpoint_slices", "endpoints[0].conditions.ready (pod web-1)", "true")).evidence)
    assert entry is not None and entry.tool == "list_endpoint_slices"


def test_evidence_about_another_pod_does_not_contradict() -> None:
    # The real netpol case: the server's endpoints are ready, the client is blocked.
    evidence = diag(("list_endpoint_slices", "endpoints[0].ready (pod web-10)", "true"),
                    ("list_endpoint_slices", "server-1 ready", "true"),
                    ("get_pod", "web-1 ready", "false")).evidence
    assert contradicting_evidence(incident(), evidence) is None


def test_pvc_phase_bound_contradicts() -> None:
    inc = incident("ProvisioningFailed", kind="PersistentVolumeClaim", name="data", pod=None,
                   container=None)
    assert contradicting_evidence(inc, diag(("get_pvc", "data status.phase", "Bound")).evidence)


# --- decide ---------------------------------------------------------------------------------


def test_model_transient_drops_the_proposal_with_a_note() -> None:
    outcome, proposal, notes = decide(incident(), "transient", FIX, [], diag(), FakeCluster())
    assert (outcome, proposal) == ("transient", None)
    assert "outcome transient" in notes[0]


def test_model_no_action_with_investigate_further_has_no_note() -> None:
    assert decide(incident(), "no_action", FURTHER, [], diag(), FakeCluster()) == (
        "no_action", None, [])


def test_contradicting_evidence_rejects_the_proposal() -> None:
    d = diag(("list_endpoint_slices", "web-1 ready", "true"))
    outcome, proposal, notes = decide(incident(), "action_required", FIX, [], d, FakeCluster())
    assert (outcome, proposal) == ("transient", None)
    assert "own evidence" in notes[-1] and "web-1 ready = true" in notes[-1]


def test_healthy_subject_now_overrides_the_model() -> None:
    outcome, proposal, notes = decide(incident(), "action_required", FIX, [], diag(),
                                      FakeCluster([pod(ready=True)]))
    assert (outcome, proposal) == ("transient", None)
    assert notes == ["no action: pod web-1 is Ready now"]


def test_broken_subject_keeps_the_proposal() -> None:
    assert decide(incident(), "action_required", FIX, [], diag(),
                  FakeCluster([pod(ready=False)])) == ("action_required", FIX, [])


@pytest.mark.parametrize("reason", ["OOMKilled", "CrashLoopBackOff"])
def test_crash_signals_are_exempt(reason: str) -> None:
    assert reason not in STATE_SIGNALS
    d = diag(("get_pod", "web-1 ready", "true"))
    assert decide(incident(reason), "action_required", FIX, [], d,
                  FakeCluster([pod(ready=True)]))[0] == "action_required"


# --- runtime wiring -----------------------------------------------------------------------

SPEC = SpecialistSpec(
    name="test-specialist", diagnosis_prompt="You are a test specialist.", tools=network_tools,
    rules=[ChangeRule("Service", re.compile(r"spec\.selector" + LABEL_KEY_SEGMENT), label_value)])


async def investigate(output: dict, kube: FakeCluster) -> Investigation:
    model = ScriptedModel([tool_call("SpecialistOutput", **output)], model_id=MODEL)
    return await run_investigation(SPEC, incident(), kube=kube, model=model,
                                   settings=Settings(agent_name="t", model_id=MODEL),
                                   prices=PriceTable({}))


async def test_runtime_returns_transient_without_a_proposal() -> None:
    result = await investigate({"outcome": "transient", "diagnosis": {
        "root_cause": "probe failed during startup", "confidence": "high"}}, FakeCluster())
    assert result.outcome == "transient" and result.proposal is None


async def test_runtime_turns_a_missing_proposal_into_investigate_further() -> None:
    result = await investigate({"diagnosis": {"root_cause": "?", "confidence": "low"}},
                               FakeCluster([pod(ready=False)]))
    assert result.outcome == "action_required"
    assert result.proposal.action == "investigate_further"


def test_prompt_explains_the_outcomes() -> None:
    assert "transient" in SPEC.system_prompt and "no_action" in SPEC.system_prompt
