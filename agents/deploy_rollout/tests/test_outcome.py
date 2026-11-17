"""hardening TASK-02: deploy_rollout uses the shared outcome rules."""

from __future__ import annotations

from test_deploy_rollout import GOOD, MODEL, NS, cluster, draft, investigate, specialist_output

from kiroween_shared.testing import ScriptedModel, obj


async def test_stuck_rollout_keeps_the_rollback() -> None:
    model = ScriptedModel([specialist_output(draft().model_dump())], model_id=MODEL)
    result = await investigate(model, cluster())
    assert result.outcome == "action_required" and result.proposal.proposed == GOOD


async def test_rollout_that_completed_since_the_incident_is_transient() -> None:
    kube = cluster()
    # The incident pod is gone and the Deployment is fully rolled out now.
    kube.add(obj("Deployment", "badimage-demo", NS, metadata={"generation": 3},
                 spec={"replicas": 1, "selector": {"matchLabels": {"app": "badimage-demo"}},
                       "template": {"spec": {"containers": [{"name": "web", "image": GOOD}]}}},
                 status={"observedGeneration": 3, "updatedReplicas": 1, "readyReplicas": 1,
                         "availableReplicas": 1}))
    model = ScriptedModel([specialist_output(draft(current=GOOD).model_dump())], model_id=MODEL)
    result = await investigate(model, kube)
    assert (result.outcome, result.proposal) == ("transient", None)
    assert "all replicas updated and available" in result.validation_notes[-1]
