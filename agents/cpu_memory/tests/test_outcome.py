"""hardening TASK-02: cpu_memory uses the shared outcome rules."""

from __future__ import annotations

from test_cpu_memory import MODEL, draft, run, specialist_output

from kiroween_cpu_memory.agent import SYSTEM_PROMPT
from kiroween_shared.testing import (
    OOM_CONTAINER,
    OOM_NAMESPACE,
    OOM_POD,
    ScriptedModel,
    oom_pod,
    tool_call,
)


def ready_oom_kube():  # type: ignore[no-untyped-def]
    from kiroween_shared.testing import oom_kube

    pod = oom_pod()
    pod["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
    for status in pod["status"]["containerStatuses"]:
        status["ready"] = True
    return oom_kube(pods={(OOM_NAMESPACE, OOM_POD): pod})


async def test_oom_pod_ready_between_restarts_still_gets_its_proposal() -> None:
    model = ScriptedModel([
        tool_call("get_pod", namespace=OOM_NAMESPACE, name=OOM_POD),
        specialist_output(draft().model_dump()),
    ], model_id=MODEL)
    result = await run(model, ready_oom_kube())
    assert result.outcome == "action_required"
    assert result.proposal.action == "adjust_resources"


async def test_no_action_has_no_proposal() -> None:
    output = specialist_output(draft().model_dump())
    output[0].input["outcome"] = "no_action"  # type: ignore[union-attr]
    model = ScriptedModel([output], model_id=MODEL)
    result = await run(model)
    assert result.outcome == "no_action" and result.proposal is None
    assert "outcome no_action" in result.validation_notes[-1]


async def test_missing_proposal_becomes_investigate_further() -> None:
    model = ScriptedModel([tool_call("SpecialistOutput", diagnosis={
        "root_cause": "unclear", "confidence": "low"})], model_id=MODEL)
    result = await run(model)
    assert result.proposal.action == "investigate_further"


def test_prompt_explains_outcomes() -> None:
    assert "action_required" in SYSTEM_PROMPT and "no_action" in SYSTEM_PROMPT
    assert OOM_CONTAINER  # fixture sanity
