"""Prometheus token and cost metrics (REQ-MET)."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from prometheus_client import CollectorRegistry, Counter, start_http_server

from .config import ConfigError
from .context import current_incident_id
from .models import Usage

logger = logging.getLogger(__name__)

REGISTRY = CollectorRegistry()
_LABELS = ("agent", "incident_id")

# prometheus_client appends _total to counters.
INPUT_TOKENS = Counter(
    "agent_llm_input_tokens", "Bedrock input tokens", _LABELS, registry=REGISTRY
)
OUTPUT_TOKENS = Counter(
    "agent_llm_output_tokens", "Bedrock output tokens", _LABELS, registry=REGISTRY
)
COST_USD = Counter(
    "agent_llm_cost_usd", "Estimated Bedrock cost in USD", _LABELS, registry=REGISTRY
)


@dataclass(frozen=True)
class Price:
    input_per_million: float
    output_per_million: float


class PriceTable:
    """USD per million tokens, by model ID. Prices are configuration, not code."""

    def __init__(self, prices: Mapping[str, Price]) -> None:
        self._prices = dict(prices)
        self._warned: set[str] = set()

    @classmethod
    def from_json(cls, raw: str) -> PriceTable:
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise ConfigError(f"PRICES_JSON is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("PRICES_JSON must be an object keyed by model id")
        prices: dict[str, Price] = {}
        for model_id, entry in data.items():
            try:
                prices[model_id] = Price(float(entry["input"]), float(entry["output"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ConfigError(
                    f"PRICES_JSON entry for {model_id!r} needs numeric 'input' and 'output'"
                ) from exc
        return cls(prices)

    def cost(self, model_id: str, input_tokens: int, output_tokens: int) -> float | None:
        price = self._prices.get(model_id)
        if price is None:
            if model_id not in self._warned:
                self._warned.add(model_id)
                logger.warning("no price configured for model %s; cost not recorded", model_id)
            return None
        return (
            input_tokens * price.input_per_million + output_tokens * price.output_per_million
        ) / 1_000_000


def record_usage(
    *,
    agent: str,
    model_id: str,
    usage: Mapping[str, Any],
    prices: PriceTable,
    incident_id: str | None = None,
) -> Usage:
    """Add one invocation's usage to the counters and return it.

    ``usage`` is Strands' ``result.metrics.accumulated_usage``. Each invocation uses a
    fresh ``Agent``, so this is that invocation's usage only.
    """
    incident = incident_id or current_incident_id()
    input_tokens = int(usage.get("inputTokens", 0) or 0)
    output_tokens = int(usage.get("outputTokens", 0) or 0)
    INPUT_TOKENS.labels(agent, incident).inc(input_tokens)
    OUTPUT_TOKENS.labels(agent, incident).inc(output_tokens)
    cost = prices.cost(model_id, input_tokens, output_tokens)
    if cost is not None:
        COST_USD.labels(agent, incident).inc(cost)
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)


def start_metrics_server(port: int) -> None:
    """Serve /metrics on its own port, apart from the API (REQ-MET-05)."""
    start_http_server(port, registry=REGISTRY)
