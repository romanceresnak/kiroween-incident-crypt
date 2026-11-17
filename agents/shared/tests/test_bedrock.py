"""TASK-03: Bedrock client construction, call budget and error translation."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError
from strands import Agent, tool

from kiroween_shared.bedrock import (
    CallBudget,
    CallBudgetExceeded,
    ModelUnavailable,
    boto_config,
    invoke,
    make_model,
)
from kiroween_shared.config import ConfigError, Settings
from kiroween_shared.testing import ScriptedModel, tool_call

EU_MODEL = "eu.anthropic.claude-sonnet-4-6"


def settings(**overrides: object) -> Settings:
    base = {"agent_name": "cpu-memory", "model_id": EU_MODEL, "max_model_calls": 15}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "ConverseStream")


def test_make_model_uses_eu_profile_in_eu_west_1() -> None:
    model = make_model(settings())
    assert model.get_config()["model_id"] == EU_MODEL
    assert model.client.meta.region_name == "eu-west-1"


def test_make_model_refuses_non_eu_model() -> None:
    with pytest.raises(ConfigError):
        make_model(settings(model_id="anthropic.claude-sonnet-4-6"))


def test_retry_config_is_adaptive_and_bounded() -> None:
    cfg = boto_config(settings(max_attempts=4))
    assert cfg.retries == {"mode": "adaptive", "max_attempts": 4}


async def test_sixteenth_model_call_is_refused() -> None:
    @tool
    def again() -> str:
        """Return a value so the model calls again."""
        return "again"

    # Every turn asks for the tool, so without the budget the loop never ends.
    model = ScriptedModel([lambda messages, specs: tool_call("again")] * 50)
    agent = Agent(model=model, tools=[again], hooks=[CallBudget(15)], callback_handler=None)

    with pytest.raises(CallBudgetExceeded):
        await invoke(agent, "loop", EU_MODEL)
    assert model.calls == 15


class _FailingModel(ScriptedModel):
    def __init__(self, code: str) -> None:
        super().__init__([])
        self.code = code

    async def stream(self, *args, **kwargs):  # type: ignore[no-untyped-def,override]
        self.calls += 1
        raise client_error(self.code)
        yield {}  # pragma: no cover


@pytest.mark.parametrize("code", ["AccessDeniedException", "ResourceNotFoundException"])
async def test_access_errors_become_model_unavailable_without_retry(code: str) -> None:
    model = _FailingModel(code)
    agent = Agent(model=model, callback_handler=None)

    with pytest.raises(ModelUnavailable) as info:
        await invoke(agent, "hi", EU_MODEL)
    assert info.value.model_id == EU_MODEL
    assert EU_MODEL in str(info.value)
    assert model.calls == 1


async def test_other_errors_are_not_translated() -> None:
    model = _FailingModel("ValidationException")
    agent = Agent(model=model, callback_handler=None)
    with pytest.raises(Exception) as info:
        await invoke(agent, "hi", EU_MODEL)
    assert not isinstance(info.value, ModelUnavailable | CallBudgetExceeded)
