"""hardening TASK-02, REQ-REG-02: the network specialist's false NetworkPolicy diagnosis.

On 17 Nov the server's readiness probe failed with "connection refused" while it booted.
The model blamed the NetworkPolicy although its own evidence showed the endpoint ready.
"""

from __future__ import annotations

from datetime import UTC, datetime

from test_network import MODEL, NS, cluster, draft

from kiroween_network.spec import SPEC
from kiroween_shared.config import Settings
from kiroween_shared.metrics import PriceTable
from kiroween_shared.models import Incident, Investigation
from kiroween_shared.specialist import run_investigation
from kiroween_shared.testing import FakeCluster, ScriptedModel, obj, tool_call


def server_incident() -> Incident:
    return Incident.model_validate({
        "incident_id": "net-fa", "source": "watcher", "reason": "Unhealthy", "namespace": NS,
        "workload": {"kind": "Deployment", "name": "netpol-server"},
        "pod": "netpol-server-1", "container": "server",
        "message": "Unhealthy: Readiness probe failed: dial tcp 10.0.1.5:8080: "
                   "connect: connection refused",
        "observed_at": datetime(2026, 11, 17, tzinfo=UTC)})


def client_incident() -> Incident:
    return Incident.model_validate({
        **server_incident().model_dump(), "incident_id": "net-real",
        "workload": {"kind": "Deployment", "name": "netpol-client"},
        "pod": "netpol-client-abc", "container": "client",
        "message": "Unhealthy: Readiness probe failed: wget: download timed out"})


def output(*evidence: tuple[str, str, str]) -> list:
    return tool_call("SpecialistOutput", outcome="action_required", diagnosis={
        "root_cause": "allow-trusted blocks traffic to the server", "confidence": "high",
        "evidence": [{"tool": t, "field": f, "value": v} for t, f, v in evidence]},
        proposal=draft().model_dump())


async def investigate(inc: Incident, model: ScriptedModel, kube: FakeCluster) -> Investigation:
    return await run_investigation(SPEC, inc, kube=kube, model=model,
                                   settings=Settings(agent_name="network", model_id=MODEL),
                                   prices=PriceTable({}))


async def test_policy_blamed_while_own_evidence_shows_the_endpoint_ready() -> None:
    model = ScriptedModel([
        tool_call("list_endpoint_slices", namespace=NS, service="netpol-server"),
        output(("list_endpoint_slices",
                "endpoints[0].conditions.ready (targetRef pod netpol-server-1)", "true"),
               ("list_network_policies", "allow-trusted ingress from app", "netpol-trusted")),
    ], model_id=MODEL)
    result = await investigate(server_incident(), model, cluster())
    assert result.outcome == "transient"
    assert result.proposal is None
    assert "own evidence shows the subject healthy" in result.validation_notes[-1]


async def test_server_ready_now_is_transient_even_without_such_evidence() -> None:
    kube = cluster()
    kube.add(obj("Pod", "netpol-server-1", NS, metadata={"labels": {"app": "netpol-server"}},
                 status={"conditions": [{"type": "Ready", "status": "True"}],
                         "containerStatuses": [{"name": "server", "ready": True}]}))
    model = ScriptedModel([output(("get_pod", "netpol-server-1 phase", "Running"))],
                          model_id=MODEL)
    result = await investigate(server_incident(), model, kube)
    assert (result.outcome, result.proposal) == ("transient", None)
    assert result.validation_notes == ["no action: pod netpol-server-1 is Ready now"]


async def test_real_block_keeps_the_fix_although_the_server_endpoint_is_ready() -> None:
    # The genuine chaos/netpol diagnosis cites the server's ready endpoint too. That
    # evidence is about another pod, so it must not cancel the client's fix.
    model = ScriptedModel([output(
        ("list_endpoint_slices", "endpoints[0].ready (pod netpol-server-1)", "true"),
        ("get_pod", "netpol-client-abc ready", "false"))], model_id=MODEL)
    result = await investigate(client_incident(), model, cluster())
    assert result.outcome == "action_required"
    assert result.proposal.action == "set_field"
    assert result.proposal.proposed == "netpol-client"


def test_prompt_tells_the_model_not_to_blame_a_policy_for_a_cleared_failure() -> None:
    assert "transient" in SPEC.system_prompt
    assert "blame an object" in " ".join(SPEC.system_prompt.split())
