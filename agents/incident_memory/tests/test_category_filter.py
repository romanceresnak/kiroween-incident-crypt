"""polish TASK-02: category filter, filter fallback, configurable minimum score, origin.

Requests go through botocore's Stubber, which checks them against the real service model.
"""

from __future__ import annotations

import boto3
import pytest
from botocore.stub import Stubber
from fastapi.testclient import TestClient
from test_incident_memory import KB, incident

from kiroween_incident_memory.api import create
from kiroween_incident_memory.memory import (
    DEFAULT_MIN_SCORE,
    FALLBACK_POOL,
    POOL,
    Memory,
    allowed_categories,
    build_query,
    category_filter,
    parse_min_score,
    to_similar,
)
from kiroween_shared.metrics import REGISTRY

URI = "s3://kiroween-prod-postmortems-255834079310/postmortems/{}.md"


def hit(score: float, category: str | None, title: str = "PM", *, source: str | None = None,
        uri: str | None = None) -> dict:
    metadata: dict = {"title": title, "fix": f"fix for {title}"}
    if category is not None:
        metadata["category"] = category
    if source is not None:
        metadata["source"] = source
    return {"content": {"text": f"# {title}\n"}, "score": score, "metadata": metadata,
            "location": {"type": "S3", "s3Location": {"uri": uri or URI.format(title)}}}


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def search(pool: int, flt: dict | None = None) -> dict:
    config: dict = {"numberOfResults": pool}
    if flt is not None:
        config["filter"] = flt
    return {"knowledgeBaseId": KB, "retrievalQuery": {"text": build_query(incident())},
            "retrievalConfiguration": {"vectorSearchConfiguration": config}}


def client_with(*steps: tuple):  # type: ignore[no-untyped-def]
    """Each step: ("ok", params, results) or ("error", params, code)."""
    client = boto3.client("bedrock-agent-runtime", region_name="eu-west-1")
    stub = Stubber(client)
    for kind, params, payload in steps:
        if kind == "ok":
            stub.add_response("retrieve", {"retrievalResults": payload}, params)
        else:
            stub.add_client_error("retrieve", service_error_code=payload, http_status_code=400,
                                  expected_params=params)
    stub.activate()
    return client, stub


def net_filter() -> dict:
    return {"orAll": [{"equals": {"key": "category", "value": "network"}},
                      {"equals": {"key": "category", "value": "general"}}]}


# --- the filter (REQ-RET-02, RET-03) --------------------------------------------------------


@pytest.mark.parametrize("category", ["cpu_memory", "network", "deploy_rollout", "storage"])
def test_a_specialist_category_allows_itself_and_general(category: str) -> None:
    assert allowed_categories(category) == (category, "general")
    assert category_filter((category, "general")) == {"orAll": [
        {"equals": {"key": "category", "value": category}},
        {"equals": {"key": "category", "value": "general"}}]}


@pytest.mark.parametrize("category", [None, "unknown", "", "nonsense"])
def test_no_category_filter_for_unknown_or_missing(category: str | None) -> None:
    assert allowed_categories(category) is None


def test_the_request_carries_the_metadata_filter() -> None:
    client, stub = client_with(("ok", search(POOL, net_filter()), []))
    assert Memory(client, KB).similar(incident(), "network") == []
    stub.assert_no_pending_responses()


def test_unknown_sends_no_filter() -> None:
    client, stub = client_with(("ok", search(POOL), []))
    Memory(client, KB).similar(incident(), "unknown")
    stub.assert_no_pending_responses()


def test_only_the_incidents_category_and_general_survive() -> None:
    # The 17 Nov case: the right postmortem at 0.71, an unrelated one at 0.70.
    results = [hit(0.71, "network", "right"), hit(0.70, "storage", "unrelated"),
               hit(0.66, "general", "generic"), hit(0.65, None, "no category"),
               hit(0.64, "cpu_memory", "other")]
    client, _ = client_with(("ok", search(POOL, net_filter()), results))
    before = sample("incident_memory_dropped_total", reason="category")
    hits = Memory(client, KB, min_score=0.5).similar(incident(), "network")
    assert [h.title for h in hits] == ["right", "generic"]
    assert sample("incident_memory_dropped_total", reason="category") == before + 3


def test_without_a_category_every_category_is_kept() -> None:
    results = [hit(0.7, "network"), hit(0.6, "storage"), hit(0.55, None)]
    client, _ = client_with(("ok", search(POOL), results))
    assert len(Memory(client, KB, min_score=0.5).similar(incident())) == 3


# --- fallback when the filter is rejected (REQ-RET-04) ---------------------------------------


def test_rejected_filter_falls_back_to_an_unfiltered_larger_pool() -> None:
    results = [hit(0.71, "network", "right"), hit(0.70, "storage", "unrelated")]
    client, stub = client_with(("error", search(POOL, net_filter()), "ValidationException"),
                               ("ok", search(FALLBACK_POOL), results))
    before = sample("incident_memory_filter_fallback_total")
    hits = Memory(client, KB, min_score=0.5).similar(incident(), "network")
    stub.assert_no_pending_responses()
    assert [h.title for h in hits] == ["right"]        # the code check still filters
    assert sample("incident_memory_filter_fallback_total") == before + 1


def test_other_errors_do_not_fall_back() -> None:
    from kiroween_incident_memory.memory import RetrieveFailed

    client, stub = client_with(("error", search(POOL, net_filter()), "AccessDeniedException"))
    with pytest.raises(RetrieveFailed, match="AccessDeniedException"):
        Memory(client, KB).similar(incident(), "network")
    stub.assert_no_pending_responses()


def test_a_validation_error_without_a_filter_is_an_error() -> None:
    from kiroween_incident_memory.memory import RetrieveFailed

    client, _ = client_with(("error", search(POOL), "ValidationException"))
    with pytest.raises(RetrieveFailed, match="ValidationException"):
        Memory(client, KB).similar(incident(), None)


# --- the minimum score (REQ-RET-05) ------------------------------------------------------------


def test_hits_below_the_minimum_score_are_dropped_and_counted() -> None:
    results = [hit(0.62, "network", "ok"), hit(0.49, "network", "weak")]
    client, _ = client_with(("ok", search(POOL, net_filter()), results))
    before = sample("incident_memory_dropped_total", reason="score")
    hits = Memory(client, KB, min_score=0.5).similar(incident(), "network")
    assert [h.title for h in hits] == ["ok"]
    assert sample("incident_memory_dropped_total", reason="score") == before + 1


def test_the_threshold_is_inclusive_and_top_k_applies_after_filtering() -> None:
    results = [hit(0.5, "network", "edge"), hit(0.9, "storage", "x"), hit(0.8, "network", "a"),
               hit(0.7, "network", "b")]
    client, _ = client_with(("ok", search(POOL, net_filter()), results))
    hits = Memory(client, KB, top_k=2, min_score=0.5).similar(incident(), "network")
    assert [h.title for h in hits] == ["a", "b"]       # 0.9 was another category


@pytest.mark.parametrize("raw,expected", [(None, DEFAULT_MIN_SCORE), ("", DEFAULT_MIN_SCORE),
                                          ("0", 0.0), ("0.65", 0.65), ("1", 1.0)])
def test_min_score_parsing(raw: str | None, expected: float) -> None:
    assert parse_min_score(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "-0.1", "1.01", "nan", "inf", "0,5"])
def test_invalid_min_score_stops_startup(raw: str) -> None:
    with pytest.raises(ValueError, match="MIN_SCORE"):
        parse_min_score(raw)


@pytest.mark.parametrize("value", [-0.1, 1.5])
def test_memory_rejects_an_out_of_range_score(value: float) -> None:
    with pytest.raises(ValueError, match="min_score"):
        Memory(object(), KB, min_score=value)


# --- the API (REQ-RET-06) ----------------------------------------------------------------------


def test_category_is_an_optional_query_parameter() -> None:
    client, stub = client_with(("ok", search(POOL, net_filter()), [hit(0.8, "network")]),
                               ("ok", search(POOL), [hit(0.8, "storage")]))
    body = incident().model_dump(mode="json")
    with TestClient(create(Memory(client, KB))) as api:
        filtered = api.post("/similar?category=network", json=body)
        plain = api.post("/similar", json=body)
    stub.assert_no_pending_responses()
    assert [h["category"] for h in filtered.json()] == ["network"]
    assert [h["category"] for h in plain.json()] == ["storage"]


def test_an_invalid_category_is_refused() -> None:
    with TestClient(create(Memory(object(), KB))) as api:
        response = api.post("/similar?category=pirates", json=incident().model_dump(mode="json"))
    assert response.status_code == 422


# --- origin (REQ-LRN-08) -------------------------------------------------------------------------


def test_origin_defaults_to_seeded() -> None:
    assert to_similar(hit(0.7, "network")).origin == "seeded"


def test_origin_is_learned_from_the_metadata_source() -> None:
    assert to_similar(hit(0.7, "network", source="learned")).origin == "learned"
    assert to_similar(hit(0.7, "network", source="Learned")).origin == "learned"
    assert to_similar(hit(0.7, "network", source="seeded")).origin == "seeded"


def test_origin_is_learned_from_the_object_key() -> None:
    learned = to_similar(hit(0.7, "network", uri="s3://b/learned/abc123.md"))
    assert learned.origin == "learned"
    assert to_similar(hit(0.7, "network", uri="s3://b/postmortems/learned/x.md")).origin == "seeded"
    assert to_similar(hit(0.7, "network", uri="s3://b/learnedish/x.md")).origin == "seeded"
