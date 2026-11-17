"""TASK-04: token and cost metrics."""

from __future__ import annotations

import logging
import uuid

import pytest
from prometheus_client import generate_latest

from kiroween_shared.config import ConfigError
from kiroween_shared.context import incident_context
from kiroween_shared.metrics import REGISTRY, Price, PriceTable, record_usage

HAIKU = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
PRICES = PriceTable({HAIKU: Price(input_per_million=1.0, output_per_million=5.0)})


def sample(name: str, agent: str, incident: str) -> float | None:
    return REGISTRY.get_sample_value(name, {"agent": agent, "incident_id": incident})


def fresh_id() -> str:
    return uuid.uuid4().hex[:12]


def test_tokens_and_cost_recorded_with_labels() -> None:
    inc = fresh_id()
    usage = record_usage(
        agent="orchestrator",
        model_id=HAIKU,
        usage={"inputTokens": 2_000, "outputTokens": 400, "totalTokens": 2_400},
        prices=PRICES,
        incident_id=inc,
    )
    assert sample("agent_llm_input_tokens_total", "orchestrator", inc) == 2_000
    assert sample("agent_llm_output_tokens_total", "orchestrator", inc) == 400
    # 2000 * 1.0/1e6 + 400 * 5.0/1e6 = 0.004
    assert sample("agent_llm_cost_usd_total", "orchestrator", inc) == pytest.approx(0.004)
    assert usage.cost_usd == pytest.approx(0.004)


def test_counters_accumulate_per_incident() -> None:
    inc = fresh_id()
    for _ in range(3):
        record_usage(
            agent="cpu-memory", model_id=HAIKU, usage={"inputTokens": 10, "outputTokens": 1},
            prices=PRICES, incident_id=inc,
        )
    assert sample("agent_llm_input_tokens_total", "cpu-memory", inc) == 30


def test_unpriced_model_records_tokens_only_and_warns_once(caplog) -> None:  # type: ignore[no-untyped-def]
    inc = fresh_id()
    table = PriceTable({})
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            usage = record_usage(
                agent="cpu-memory", model_id="eu.unpriced", usage={"inputTokens": 7},
                prices=table, incident_id=inc,
            )
    assert usage.cost_usd is None
    assert sample("agent_llm_input_tokens_total", "cpu-memory", inc) == 14
    assert sample("agent_llm_cost_usd_total", "cpu-memory", inc) is None
    assert sum("no price configured" in r.message for r in caplog.records) == 1


def test_usage_outside_an_incident_is_labelled_none() -> None:
    before = sample("agent_llm_input_tokens_total", "watcher-probe", "none") or 0
    record_usage(agent="watcher-probe", model_id=HAIKU, usage={"inputTokens": 5}, prices=PRICES)
    assert sample("agent_llm_input_tokens_total", "watcher-probe", "none") == before + 5


def test_incident_id_comes_from_context() -> None:
    inc = fresh_id()
    with incident_context(inc):
        record_usage(agent="orchestrator", model_id=HAIKU, usage={"inputTokens": 3}, prices=PRICES)
    assert sample("agent_llm_input_tokens_total", "orchestrator", inc) == 3


def test_missing_usage_keys_count_as_zero() -> None:
    inc = fresh_id()
    usage = record_usage(agent="o", model_id=HAIKU, usage={}, prices=PRICES, incident_id=inc)
    assert (usage.input_tokens, usage.output_tokens) == (0, 0)


def test_exposed_metric_names_match_requirements() -> None:
    text = generate_latest(REGISTRY).decode()
    for name in ("agent_llm_input_tokens_total", "agent_llm_output_tokens_total",
                 "agent_llm_cost_usd_total"):
        assert f"# TYPE {name} counter" in text
        assert f"\n{name}{{agent=" in text


def test_price_table_from_json() -> None:
    table = PriceTable.from_json('{"eu.m": {"input": 3, "output": 15}}')
    assert table.cost("eu.m", 1_000_000, 0) == 3.0


@pytest.mark.parametrize("raw", ["not json", "[]", '{"eu.m": {"input": 1}}', '{"eu.m": 5}'])
def test_bad_price_json_rejected(raw: str) -> None:
    with pytest.raises(ConfigError):
        PriceTable.from_json(raw)
