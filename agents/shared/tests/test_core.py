"""TASK-02: configuration, data models, context and the server helper."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from kiroween_shared.config import ConfigError, Settings, require_eu_model
from kiroween_shared.context import NO_INCIDENT, current_incident_id, incident_context
from kiroween_shared.models import (
    DELIVERY,
    Incident,
    Proposal,
    ProposalDraft,
    Usage,
)
from kiroween_shared.server import JsonFormatter, create_app

EU_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"


def incident_payload(**overrides: object) -> dict:
    payload = {
        "incident_id": "abc123",
        "source": "watcher",
        "reason": "OOMKilled",
        "namespace": "chaos-oom",
        "workload": {"kind": "Deployment", "name": "oom-demo"},
        "pod": "oom-demo-7d9f-abcde",
        "container": "app",
        "message": "",
        "observed_at": datetime(2026, 11, 17, tzinfo=UTC).isoformat(),
    }
    payload.update(overrides)
    return payload


# --- config ---------------------------------------------------------------


def test_eu_model_accepted() -> None:
    assert require_eu_model(EU_MODEL) == EU_MODEL


@pytest.mark.parametrize(
    "model_id",
    ["", "anthropic.claude-haiku-4-5", "us.anthropic.claude-haiku-4-5", "global.x", "EU.x"],
)
def test_non_eu_model_rejected(model_id: str) -> None:
    with pytest.raises(ConfigError, match="EU cross-region"):
        require_eu_model(model_id)


def test_settings_from_env_refuses_non_eu_model() -> None:
    with pytest.raises(ConfigError):
        Settings.from_env("orchestrator", {"MODEL_ID": "us.anthropic.claude-haiku-4-5"})


def test_settings_defaults() -> None:
    s = Settings.from_env("orchestrator", {"MODEL_ID": EU_MODEL})
    assert (s.region, s.api_port, s.metrics_port) == ("eu-west-1", 8080, 9090)
    assert s.max_model_calls == 15


def test_api_and_metrics_port_must_differ() -> None:
    with pytest.raises(ConfigError, match="must differ"):
        Settings.from_env("x", {"MODEL_ID": EU_MODEL, "API_PORT": "9090"})


@pytest.mark.parametrize("value", ["abc", "0", "-1"])
def test_bad_integer_rejected(value: str) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env("x", {"MODEL_ID": EU_MODEL, "MAX_MODEL_CALLS": value})


# --- models ---------------------------------------------------------------


def test_valid_incident_parses() -> None:
    incident = Incident.model_validate(incident_payload())
    assert incident.workload.name == "oom-demo"


@pytest.mark.parametrize(
    "overrides",
    [
        {"namespace": "Bad_Namespace"},
        {"container": ""},
        {"incident_id": "../../etc"},
        {"workload": {"kind": "deployment", "name": "x"}},
        {"unexpected": "field"},
        {"message": "x" * 2001},
    ],
)
def test_invalid_incident_rejected(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        Incident.model_validate(incident_payload(**overrides))


def test_proposal_cannot_skip_approval() -> None:
    with pytest.raises(ValidationError):
        Proposal(action="none", requires_approval=False)  # type: ignore[arg-type]


def test_proposal_delivery_is_fixed() -> None:
    with pytest.raises(ValidationError):
        Proposal(action="none", delivery="kubectl apply now")  # type: ignore[arg-type]
    assert Proposal(action="none").delivery == DELIVERY


def test_proposal_from_draft_adds_approval() -> None:
    proposal = Proposal(**ProposalDraft(action="investigate_further").model_dump())
    assert proposal.requires_approval is True


def test_usage_addition_keeps_unknown_cost_unknown() -> None:
    assert (Usage(input_tokens=1) + Usage(output_tokens=2)).cost_usd is None
    assert (Usage(cost_usd=0.5) + Usage()).cost_usd == 0.5


# --- context --------------------------------------------------------------


def test_incident_context_sets_and_restores() -> None:
    assert current_incident_id() == NO_INCIDENT
    with incident_context("inc-1"):
        assert current_incident_id() == "inc-1"
    assert current_incident_id() == NO_INCIDENT


def test_json_log_line_carries_incident_id() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    with incident_context("inc-9"):
        line = json.loads(JsonFormatter().format(record))
    assert line["msg"] == "hello world"
    assert line["incident_id"] == "inc-9"


# --- server ---------------------------------------------------------------


def test_health_and_readiness() -> None:
    state = {"ready": False}
    client = TestClient(create_app("t", ready=lambda: state["ready"]))
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 503
    state["ready"] = True
    assert client.get("/readyz").status_code == 200


def test_api_app_does_not_serve_metrics_or_docs() -> None:
    client = TestClient(create_app("t", ready=lambda: True))
    for path in ("/metrics", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 404
