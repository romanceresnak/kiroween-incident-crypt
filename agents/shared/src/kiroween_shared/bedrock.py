"""Bedrock model construction and invocation guards (REQ-BED)."""

from __future__ import annotations

import logging
from typing import Any

from botocore.config import Config
from botocore.exceptions import ClientError
from strands.hooks import BeforeModelCallEvent, HookProvider, HookRegistry
from strands.models import BedrockModel

from .config import Settings, require_eu_model

logger = logging.getLogger(__name__)

# Errors that a retry cannot fix (REQ-BED-05).
NO_RETRY_CODES = frozenset({"AccessDeniedException", "ResourceNotFoundException"})


class ModelUnavailable(RuntimeError):
    """The configured model cannot be called with this role. Not retried."""

    def __init__(self, model_id: str, code: str) -> None:
        super().__init__(f"Bedrock model {model_id} is not available to this role ({code})")
        self.model_id = model_id
        self.code = code


class CallBudgetExceeded(RuntimeError):
    """An invocation made more model calls than allowed (REQ-BED-07)."""


class CallBudget(HookProvider):
    """Counts model calls in one invocation and stops it past the limit.

    Create one per invocation; each ``Agent`` is also created per invocation.
    """

    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self.calls = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self._before_model_call)

    def _before_model_call(self, event: BeforeModelCallEvent) -> None:
        self.calls += 1
        if self.calls > self.max_calls:
            raise CallBudgetExceeded(
                f"more than {self.max_calls} model calls in one invocation; stopping"
            )


def boto_config(settings: Settings) -> Config:
    # Adaptive retry handles throttling with backoff, up to max_attempts (REQ-BED-06).
    # botocore never retries AccessDenied or ResourceNotFound.
    return Config(
        retries={"mode": "adaptive", "max_attempts": settings.max_attempts},
        connect_timeout=10,
        read_timeout=120,
    )


def make_model(settings: Settings) -> BedrockModel:
    """Bedrock model for one agent, called through the Converse API (REQ-BED-01).

    No credentials are passed: boto3's default chain picks up EKS Pod Identity
    (REQ-BED-04).
    """
    require_eu_model(settings.model_id)
    return BedrockModel(
        model_id=settings.model_id,
        region_name=settings.region,
        boto_client_config=boto_config(settings),
        max_tokens=settings.max_tokens,
    )


def _walk(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain and len(chain) < 10:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def client_error_code(exc: BaseException) -> str | None:
    for item in _walk(exc):
        if isinstance(item, ClientError):
            return item.response.get("Error", {}).get("Code")
    return None


def find_in_chain[E: BaseException](exc: BaseException, kind: type[E]) -> E | None:
    for item in _walk(exc):
        if isinstance(item, kind):
            return item
    return None


async def invoke(agent: Any, prompt: str, model_id: str) -> Any:
    """Run ``agent`` and translate model errors into the two cases callers handle."""
    try:
        return await agent.invoke_async(prompt)
    except Exception as exc:
        budget = find_in_chain(exc, CallBudgetExceeded)
        if budget is not None:
            raise budget from exc
        code = client_error_code(exc)
        if code in NO_RETRY_CODES:
            raise ModelUnavailable(model_id, code) from exc
        raise
