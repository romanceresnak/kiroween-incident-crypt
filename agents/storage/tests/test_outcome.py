"""hardening TASK-02: storage uses the shared outcome rules."""

from __future__ import annotations

from test_storage import MODEL, cluster, draft, investigate, pvc, specialist_output

from kiroween_shared.testing import ScriptedModel, tool_call


async def test_pending_pvc_keeps_the_fix() -> None:
    model = ScriptedModel([specialist_output(draft().model_dump())], model_id=MODEL)
    result = await investigate(model, cluster())
    assert result.outcome == "action_required" and result.proposal.proposed == "gp2"


async def test_pvc_bound_since_the_incident_is_transient() -> None:
    kube = cluster()
    kube.add(pvc("data", "does-not-exist", "Bound"))
    model = ScriptedModel([specialist_output(draft().model_dump())], model_id=MODEL)
    result = await investigate(model, kube)
    assert (result.outcome, result.proposal) == ("transient", None)


async def test_evidence_of_a_bound_claim_rejects_the_proposal() -> None:
    model = ScriptedModel([tool_call("SpecialistOutput", proposal=draft().model_dump(),
                                     diagnosis={"root_cause": "class missing",
                                                "confidence": "high",
                                                "evidence": [{"tool": "get_pvc",
                                                              "field": "data status.phase",
                                                              "value": "Bound"}]})],
                          model_id=MODEL)
    result = await investigate(model, cluster())
    assert (result.outcome, result.proposal) == ("transient", None)
    assert "own evidence" in result.validation_notes[-1]
