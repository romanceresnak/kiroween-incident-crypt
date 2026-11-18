"""Find past postmortems that look like an incident (mui:REQ-MEM-03, MEM-04, polish REQ-RET).

This is search, not reasoning: one Bedrock Knowledge Base ``Retrieve`` call per incident,
no model call. The specialist that receives the hits does the reasoning.

Relevance comes from the postmortem's ``category`` metadata, not from the score: a live test
scored the right postmortem 0.71 and an unrelated one 0.70. A hit is kept only if its
category is the incident's category or ``general``; the score threshold only removes weak
matches.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from botocore.exceptions import ClientError
from prometheus_client import Counter

from kiroween_shared.metrics import REGISTRY
from kiroween_shared.models import Incident, SimilarIncident

logger = logging.getLogger(__name__)

REQUESTS = Counter("incident_memory_requests", "Retrieve calls by result", ["result"],
                   registry=REGISTRY)
RESULTS = Counter("incident_memory_results", "Similar incidents returned", registry=REGISTRY)
DROPPED = Counter("incident_memory_dropped", "Hits dropped after Retrieve, by reason",
                  ["reason"], registry=REGISTRY)
FILTER_FALLBACK = Counter("incident_memory_filter_fallback",
                          "Retrieves repeated without the metadata filter because the "
                          "Knowledge Base rejected it", registry=REGISTRY)

MAX_QUERY = 1000
GENERAL = "general"
# Categories a specialist handles. Only these select a filter; "unknown" selects none.
SPECIALIST_CATEGORIES = frozenset({"cpu_memory", "network", "deploy_rollout", "storage"})
# Candidates fetched before filtering by score and truncating to top_k.
POOL = 10
FALLBACK_POOL = 25
DEFAULT_MIN_SCORE = 0.5

_HEADING = re.compile(r"^# (.+)$", re.M)
_FIX = re.compile(r"^## Fix\s*\n(.+?)(?=^## |\Z)", re.M | re.S)
_LEARNED_KEY = re.compile(r"^s3://[^/]+/learned/")


class RetrieveFailed(RuntimeError):
    pass


def parse_min_score(raw: str | None) -> float:
    """MIN_SCORE: a number from 0 to 1. Anything else stops the pod at startup."""
    if raw is None or raw.strip() == "":
        return DEFAULT_MIN_SCORE
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"MIN_SCORE must be a number from 0 to 1, got {raw!r}") from None
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"MIN_SCORE must be a number from 0 to 1, got {raw!r}")
    return value


def allowed_categories(category: str | None) -> tuple[str, ...] | None:
    """Categories a hit may have, or None for no category filter (REQ-RET-02, RET-03)."""
    if category in SPECIALIST_CATEGORIES:
        return (category, GENERAL)
    return None


def category_filter(allowed: tuple[str, ...]) -> dict[str, Any]:
    """Retrieve metadata filter: category is one of ``allowed``."""
    return {"orAll": [{"equals": {"key": "category", "value": c}} for c in allowed]}


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
    uri = _text(location.get("uri"), 300)
    learned = _text(metadata.get("source"), 20).lower() == "learned" or bool(
        _LEARNED_KEY.match(uri))
    return SimilarIncident(
        title=_text(metadata.get("title") or (heading.group(1) if heading else "")
                    or "Untitled postmortem", 200),
        category=_text(metadata.get("category"), 40) or None,
        score=float(result.get("score") or 0.0),
        fix=_text(metadata.get("fix") or (fix.group(1) if fix else ""), 1000),
        source=uri,
        excerpt=_text(content, 400),
        origin="learned" if learned else "seeded",
    )


class Memory:
    def __init__(self, client: Any, knowledge_base_id: str, *, top_k: int = 3,
                 min_score: float = DEFAULT_MIN_SCORE) -> None:
        if not knowledge_base_id:
            raise ValueError("KNOWLEDGE_BASE_ID is required")
        if not 0.0 <= min_score <= 1.0:
            raise ValueError(f"min_score must be from 0 to 1, got {min_score}")
        self.client = client
        self.knowledge_base_id = knowledge_base_id
        self.top_k = max(1, min(top_k, 5))
        self.min_score = min_score

    def _retrieve(self, incident: Incident, allowed: tuple[str, ...] | None) -> list[dict]:
        def call(pool: int, with_filter: bool) -> dict[str, Any]:
            search: dict[str, Any] = {"numberOfResults": pool}
            if with_filter and allowed is not None:
                search["filter"] = category_filter(allowed)
            return self.client.retrieve(
                knowledgeBaseId=self.knowledge_base_id,
                retrievalQuery={"text": build_query(incident)},
                retrievalConfiguration={"vectorSearchConfiguration": search},
            )

        try:
            try:
                response = call(POOL, True)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if allowed is None or code != "ValidationException":
                    raise
                # The KB rejected the metadata filter (REQ-RET-04): ask without it, with a
                # bigger pool, and let the category check below do the filtering.
                logger.warning("metadata filter rejected (%s); retrying without it", code)
                FILTER_FALLBACK.inc()
                response = call(FALLBACK_POOL, False)
        except Exception as exc:
            REQUESTS.labels(result="error").inc()
            logger.error("Retrieve failed: %s", type(exc).__name__)
            raise RetrieveFailed(_error_name(exc)) from exc
        return list(response.get("retrievalResults") or [])

    def similar(self, incident: Incident, category: str | None = None) -> list[SimilarIncident]:
        allowed = allowed_categories(category)
        hits = [to_similar(r) for r in self._retrieve(incident, allowed)]
        kept: list[SimilarIncident] = []
        wrong_category = weak = 0
        for hit in hits:
            if allowed is not None and hit.category not in allowed:
                wrong_category += 1
            elif hit.score < self.min_score:
                weak += 1
            else:
                kept.append(hit)
        kept = sorted(kept, key=lambda h: h.score, reverse=True)[: self.top_k]
        REQUESTS.labels(result="ok").inc()
        RESULTS.inc(len(kept))
        DROPPED.labels(reason="category").inc(wrong_category)
        DROPPED.labels(reason="score").inc(weak)
        logger.info("incident %s (category %s): kept %d of %d (dropped %d other category, "
                    "%d below %.2f)", incident.incident_id, category or "any", len(kept),
                    len(hits), wrong_category, weak, self.min_score)
        return kept


def _error_name(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code") or type(exc).__name__
    return type(exc).__name__
