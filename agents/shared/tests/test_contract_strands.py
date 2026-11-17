"""Contract tests: the Strands behaviour the design depends on (design §15).

Each test builds a real ``strands.Agent`` with a scripted model, so a Strands upgrade
that changes one of these behaviours fails here first.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from strands import Agent, tool

from kiroween_shared.testing import ScriptedModel, text, tool_call


class Verdict(BaseModel):
    label: str
    score: int


async def test_tool_registers_and_runs() -> None:
    calls: list[str] = []

    @tool
    def lookup(name: str) -> str:
        """Look up a value by name."""
        calls.append(name)
        return f"value-of-{name}"

    model = ScriptedModel([tool_call("lookup", name="alpha"), text("finished")])
    agent = Agent(model=model, tools=[lookup], callback_handler=None)
    result = await agent.invoke_async("go")

    assert calls == ["alpha"]
    assert "lookup" in model.tool_specs_seen[0]
    assert "lookup" in result.metrics.tool_metrics
    assert result.metrics.tool_metrics["lookup"].call_count == 1


async def test_structured_output_model_returns_parsed_object() -> None:
    # The structured output tool is named after the Pydantic class.
    model = ScriptedModel([tool_call("Verdict", label="ok", score=3)])
    agent = Agent(model=model, structured_output_model=Verdict, callback_handler=None)
    result = await agent.invoke_async("classify")

    assert isinstance(result.structured_output, Verdict)
    assert result.structured_output == Verdict(label="ok", score=3)


async def test_accumulated_usage_has_input_and_output_tokens() -> None:
    model = ScriptedModel([text("hello")], input_tokens=123, output_tokens=45)
    agent = Agent(model=model, callback_handler=None)
    result = await agent.invoke_async("hi")

    usage = result.metrics.accumulated_usage
    assert usage["inputTokens"] == 123
    assert usage["outputTokens"] == 45


async def test_tool_not_called_is_absent_from_tool_metrics() -> None:
    @tool
    def unused() -> str:
        """Never called."""
        return "x"

    agent = Agent(model=ScriptedModel([text("no tool")]), tools=[unused], callback_handler=None)
    result = await agent.invoke_async("hi")
    assert result.metrics.tool_metrics.get("unused") is None or (
        result.metrics.tool_metrics["unused"].call_count == 0
    )


async def test_exception_in_before_model_call_hook_stops_invocation() -> None:
    from strands.hooks import BeforeModelCallEvent, HookProvider

    class Boom(RuntimeError):
        pass

    class StopAtOnce(HookProvider):
        def register_hooks(self, registry, **kwargs):  # type: ignore[no-untyped-def]
            registry.add_callback(BeforeModelCallEvent, self._before)

        def _before(self, event):  # type: ignore[no-untyped-def]
            raise Boom("stop")

    model = ScriptedModel([text("never")])
    agent = Agent(model=model, hooks=[StopAtOnce()], callback_handler=None)
    with pytest.raises(Exception) as info:
        await agent.invoke_async("hi")
    chain = []
    exc: BaseException | None = info.value
    while exc is not None and len(chain) < 10:
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    assert any(isinstance(item, Boom) for item in chain)
    assert model.calls == 0
