"""A scripted Strands model for tests. It never makes a network call.

Each call to ``stream`` replays the next scripted turn as Bedrock Converse stream
events, so the real Strands event loop, tool execution and structured output run
unchanged.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

from strands.models.model import Model


@dataclass
class Text:
    text: str


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any] = field(default_factory=dict)


Turn = list[Text | ToolCall]
TurnFactory = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None], Turn]


def text(value: str) -> Turn:
    return [Text(value)]


def tool_call(tool_name: str, /, **arguments: Any) -> Turn:
    return [ToolCall(tool_name, arguments)]


class ScriptedModel(Model):
    def __init__(
        self,
        turns: list[Turn | TurnFactory],
        *,
        model_id: str = "eu.test.scripted-v1",
        input_tokens: int = 100,
        output_tokens: int = 20,
    ) -> None:
        self._turns = list(turns)
        self._config: dict[str, Any] = {"model_id": model_id}
        self._ids = itertools.count(1)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.calls = 0
        self.tool_specs_seen: list[list[str]] = []

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return self._config

    async def structured_output(  # type: ignore[override]
        self, output_model: Any, prompt: Any, system_prompt: str | None = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError("structured output goes through the tool path")
        yield {}  # pragma: no cover - makes this an async generator

    async def stream(  # type: ignore[override]
        self,
        messages: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.calls += 1
        self.tool_specs_seen.append([spec["name"] for spec in tool_specs or []])
        entry = self._turns.pop(0) if self._turns else text("done")
        turn = entry(messages, tool_specs) if callable(entry) else entry

        yield {"messageStart": {"role": "assistant"}}
        stop_reason = "end_turn"
        for block in turn:
            if isinstance(block, Text):
                yield {"contentBlockDelta": {"delta": {"text": block.text}}}
                yield {"contentBlockStop": {}}
            else:
                tool_use_id = f"tooluse_{next(self._ids)}"
                yield {
                    "contentBlockStart": {
                        "start": {"toolUse": {"toolUseId": tool_use_id, "name": block.name}}
                    }
                }
                tool_input = json.dumps(block.input)
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": tool_input}}}}
                yield {"contentBlockStop": {}}
                stop_reason = "tool_use"
        yield {"messageStop": {"stopReason": stop_reason}}
        yield {
            "metadata": {
                "usage": {
                    "inputTokens": self.input_tokens,
                    "outputTokens": self.output_tokens,
                    "totalTokens": self.input_tokens + self.output_tokens,
                },
                "metrics": {"latencyMs": 1},
            }
        }
