"""Find past postmortems that look like an incident (memory-and-ui REQ-MEM-03, MEM-04).

This is search, not reasoning: one Bedrock Knowledge Base ``Retrieve`` call per incident,
no model call. The specialist that receives the hits does the reasoning.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from prometheus_client import Counter

from kiroween_shared.metrics import REGISTRY
from kiroween_shared.models import Incident, SimilarIncident

logger = logging.getLogger(__name__)

REQUESTS = Counter("incident_memory_requests", "Retrieve calls by result", ["result"],
                   registry=REGISTRY)
RESULTS = Counter("incident_memory_results", "Similar incidents returned", registry=REGISTRY)

MAX_QUERY = 1000
_HEADING = re.compile(r"^# (.+)$", re.M)
_FIX = re.compile(r"^## Fix\s*\n(.+?)(?=^## |\Z)", re.M | re.S)


class RetrieveFailed(RuntimeError):
    pass


def build_query(incident: Incident) -> str:
    """The search text, built by code from the incident (REQ-MEM-04)."""
    parts = [f"{incident.reason} on a Kubernetes {incident.workload.kind}"]
    if incident.message:
        parts.append(incident.message)
    return ". ".join(parts)[:MAX_QUERY]


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def to_similar(result: dict[str, Any]) -> SimilarIncident:
    """Map one Retrieve result. Prefer the metadata sidecar; fall back to the chunk text."""
    content = (result.get("content") or {}).get("text") or ""
    metadata = result.get("metadata") or {}
    heading = _HEADING.search(content)
    fix = _FIX.search(content)
    location = (result.get("location") or {}).get("s3Location") or {}
    return SimilarIncident(
        title=_text(metadata.get("title") or (heading.group(1) if heading else "")
                    or "Untitled postmortem", 200),
        category=_text(metadata.get("category"), 40) or None,
        score=float(result.get("score") or 0.0),
        fix=_text(metadata.get("fix") or (fix.group(1) if fix else ""), 1000),
        source=_text(location.get("uri"), 300),
        excerpt=_text(content, 400),
    )


class Memory:
    def __init__(self, client: Any, knowledge_base_id: str, *, top_k: int = 3,
                 min_score: float = 0.3) -> None:
        if not knowledge_base_id:
            raise ValueError("KNOWLEDGE_BASE_ID is required")
        self.client = client
        self.knowledge_base_id = knowledge_base_id
        self.top_k = max(1, min(top_k, 5))
        self.min_score = min_score

    def similar(self, incident: Incident) -> list[SimilarIncident]:
        try:
            response = self.client.retrieve(
                knowledgeBaseId=self.knowledge_base_id,
                retrievalQuery={"text": build_query(incident)},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {"numberOfResults": self.top_k}},
            )
        except Exception as exc:
            REQUESTS.labels(result="error").inc()
            logger.error("Retrieve failed: %s", type(exc).__name__)
            raise RetrieveFailed(type(exc).__name__) from exc
        hits = [to_similar(r) for r in response.get("retrievalResults") or []]
        hits = sorted((h for h in hits if h.score >= self.min_score),
                      key=lambda h: h.score, reverse=True)[: self.top_k]
        REQUESTS.labels(result="ok").inc()
        RESULTS.inc(len(hits))
        logger.info("incident %s: %d similar incidents", incident.incident_id, len(hits))
        return hits
