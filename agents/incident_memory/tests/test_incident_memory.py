"""memory-and-ui TASK-03: the incident-memory agent.

Retrieve is exercised through botocore's Stubber, which validates the request against the
real bedrock-agent-runtime service model; no request leaves the process.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber
from fastapi.testclient import TestClient

from kiroween_incident_memory.api import create
from kiroween_incident_memory.memory import POOL, Memory, build_query, to_similar
from kiroween_shared.metrics import REGISTRY
from kiroween_shared.models import Incident

KB = "VIBAT27SDH"
SRC = Path(__file__).parents[1] / "src" / "kiroween_incident_memory"


def incident(**kw: object) -> Incident:
    return Incident.model_validate({
        "incident_id": "im-1", "source": "watcher", "reason": "ProvisioningFailed",
        "namespace": "chaos-pvc", "workload": {"kind": "PersistentVolumeClaim", "name": "data"},
        "message": 'ProvisioningFailed: storageclass.storage.k8s.io "does-not-exist" not found',
        "observed_at": datetime(2026, 11, 17, tzinfo=UTC), **kw})


CHUNK = ("# PM-2026-085: analytics-cache stuck Pending\n\n## Summary\n\nThe claim stayed "
         "Pending.\n\n## Fix\n\nChanged spec.storageClassName to gp3 and recreated the claim.\n"
         "\n## Prevention\n\nPolicy check.\n")


def result(score: float, metadata: dict | None = None, text: str = CHUNK,
           doc: str = "07") -> dict:
    out: dict = {"content": {"text": text}, "score": score,
                 "location": {"type": "S3", "s3Location": {
                     "uri": f"s3://kiroween-prod-postmortems-255834079310/postmortems/{doc}.md"}}}
    if metadata is not None:
        out["metadata"] = metadata
    return out


META = {"title": "PM-2026-085: analytics-cache stuck Pending on a StorageClass that did not exist",
        "category": "storage", "signal": "ProvisioningFailed",
        "fix": "Changed spec.storageClassName to a class that exists and recreated the claim.",
        "x-amz-bedrock-kb-source-uri": "s3://.../07.md"}


def stubbed(results: list[dict], *, pool: int = POOL, error: str | None = None,
            query: str | None = None, flt: dict | None = None):  # type: ignore[no-untyped-def]
    client = boto3.client("bedrock-agent-runtime", region_name="eu-west-1")
    stub = Stubber(client)
    search: dict = {"numberOfResults": pool}
    if flt is not None:
        search["filter"] = flt
    expected = {"knowledgeBaseId": KB,
                "retrievalQuery": {"text": query or build_query(incident())},
                "retrievalConfiguration": {"vectorSearchConfiguration": search}}
    if error:
        stub.add_client_error("retrieve", service_error_code=error, http_status_code=403,
                              expected_params=expected)
    else:
        stub.add_response("retrieve", {"retrievalResults": results}, expected)
    stub.activate()
    return client, stub


def test_query_is_built_from_reason_kind_and_message() -> None:
    q = build_query(incident())
    assert q.startswith("ProvisioningFailed on a Kubernetes PersistentVolumeClaim. ")
    assert "does-not-exist" in q
    assert len(build_query(incident(message="x" * 2000))) == 1000


def test_query_does_not_include_namespace_or_workload_names() -> None:
    # Names are specific to this cluster; they only add noise to a semantic search.
    q = build_query(incident(message=""))
    assert "chaos-pvc" not in q and "data" not in q.split()


def test_retrieve_request_and_metadata_mapping() -> None:
    client, stub = stubbed([result(0.62, META, doc="a"), result(0.81, META)])
    hits = Memory(client, KB).similar(incident())
    stub.assert_no_pending_responses()
    assert [h.score for h in hits] == [0.81, 0.62]  # best first
    hit = hits[0]
    assert hit.title.startswith("PM-2026-085") and hit.category == "storage"
    assert hit.fix.startswith("Changed spec.storageClassName")
    assert hit.source.endswith("/postmortems/07.md")
    assert hit.excerpt.startswith("# PM-2026-085")


def test_fallback_to_the_chunk_text_without_metadata() -> None:
    hit = to_similar(result(0.5))
    assert hit.title == "PM-2026-085: analytics-cache stuck Pending"
    assert hit.fix == "Changed spec.storageClassName to gp3 and recreated the claim."
    assert hit.category is None


def test_chunk_without_heading_or_fix() -> None:
    hit = to_similar(result(0.5, text="Evidence: the claim is Pending."))
    assert hit.title == "Untitled postmortem" and hit.fix == ""


def test_low_scores_are_dropped_and_top_k_respected() -> None:
    client, stub = stubbed([result(0.9, META, doc="a"), result(0.29, META, doc="b"),
                            result(0.5, META, doc="c")])
    hits = Memory(client, KB, top_k=2, min_score=0.3).similar(incident())
    assert [h.score for h in hits] == [0.9, 0.5]


def test_top_k_is_capped_at_five() -> None:
    assert Memory(object(), KB, top_k=50).top_k == 5


def test_missing_knowledge_base_id_is_refused() -> None:
    with pytest.raises(ValueError, match="KNOWLEDGE_BASE_ID"):
        Memory(object(), "")


def test_api_returns_hits_and_counts_them() -> None:
    before = REGISTRY.get_sample_value("incident_memory_requests_total", {"result": "ok"}) or 0
    client, _ = stubbed([result(0.7, META)])
    with TestClient(create(Memory(client, KB))) as api:
        response = api.post("/similar", json=incident().model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()[0]["category"] == "storage"
    assert REGISTRY.get_sample_value("incident_memory_requests_total", {"result": "ok"}) == \
        before + 1


def test_retrieve_error_is_a_503_without_details_of_the_request() -> None:
    client, _ = stubbed([], error="AccessDeniedException")
    with TestClient(create(Memory(client, KB))) as api:
        response = api.post("/similar", json=incident().model_dump(mode="json"))
    assert response.status_code == 503
    assert response.json()["detail"] == "knowledge base retrieve failed (AccessDeniedException)"
    assert REGISTRY.get_sample_value("incident_memory_requests_total", {"result": "error"})


def test_api_rejects_a_malformed_incident() -> None:
    with TestClient(create(Memory(object(), KB))) as api:
        assert api.post("/similar", json={"incident_id": "x"}).status_code == 422


def test_health_endpoints() -> None:
    with TestClient(create(Memory(object(), KB))) as api:
        assert api.get("/healthz").status_code == 200
        assert api.get("/readyz").status_code == 200


def test_no_model_and_no_kubernetes_imports() -> None:
    imported = set()
    for path in SRC.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not {m for m in imported if m.startswith(("strands", "kubernetes",
                                                      "kiroween_shared.k8s",
                                                      "kiroween_shared.bedrock"))}
