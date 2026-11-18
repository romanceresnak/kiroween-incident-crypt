"""polish: only the best chunk of each source document is returned.

Retrieve returns chunks, and a document can have several that score well. On 18 Nov the
filtered result for the netpol client held PM-2026-058 three times and PM-2026-066 twice.
"""

from __future__ import annotations

import pytest
from test_category_filter import client_with, hit, net_filter, sample, search
from test_incident_memory import (
    KB,
    incident,  # noqa: I001  (grouped with the fixtures above)
)

from kiroween_incident_memory.memory import POOL, Memory, document_key, to_similar

BASE = "s3://kiroween-prod-postmortems-255834079310/"
DOC_058 = BASE + "postmortems/03-networkpolicy-blocks-invoice-worker.md"
DOC_066 = BASE + "postmortems/04-coredns-forwarding-loop.md"


def chunk(score: float, category: str | None, title: str, uri: str | None, text: str = "") -> dict:
    out = hit(score, category, title, uri=uri)
    if text:
        out["content"]["text"] = text
    if uri is None:
        out["location"] = {"type": "S3"}   # a valid location that names no document
    return out


def netpol_chunks() -> list[dict]:
    """The 18 Nov filtered result, in the order Retrieve returned it."""
    t58 = "PM-2026-058: NetworkPolicy blocked the billing worker"
    t66 = "PM-2026-066: CoreDNS forwarding loop"
    return [chunk(0.7248, "network", t58, DOC_058, "58 chunk A: client probe timed out"),
            chunk(0.7071, "network", t66, DOC_066, "66 chunk A: lookups failed"),
            chunk(0.6737, "network", t58, DOC_058, "58 chunk B: policy selects app"),
            chunk(0.6141, "network", t66, DOC_066, "66 chunk B: loop plugin"),
            chunk(0.6090, "network", t58, DOC_058, "58 chunk C: fix and prevention")]


def run(results: list[dict], *, top_k: int = 3, min_score: float = 0.5,
        category: str | None = "network") -> list:
    flt = net_filter() if category == "network" else None
    client, stub = client_with(("ok", search(POOL, flt), results))
    hits = Memory(client, KB, top_k=top_k, min_score=min_score).similar(incident(), category)
    stub.assert_no_pending_responses()
    return hits


def test_the_live_result_gives_each_document_once_with_its_best_chunk() -> None:
    hits = run(netpol_chunks())
    assert [(h.source, h.score) for h in hits] == [(DOC_058, 0.7248), (DOC_066, 0.7071)]
    assert [h.excerpt for h in hits] == ["58 chunk A: client probe timed out",
                                         "66 chunk A: lookups failed"]


def test_it_does_not_depend_on_the_order_chunks_arrive_in() -> None:
    hits = run(list(reversed(netpol_chunks())))
    assert [(h.source, h.score) for h in hits] == [(DOC_058, 0.7248), (DOC_066, 0.7071)]
    assert hits[0].excerpt.startswith("58 chunk A")      # the best chunk, not the first seen


def test_top_k_counts_documents_not_chunks() -> None:
    docs = [chunk(0.90, "network", "A", BASE + "a.md"), chunk(0.89, "network", "A", BASE + "a.md"),
            chunk(0.88, "network", "A", BASE + "a.md"), chunk(0.80, "network", "B", BASE + "b.md"),
            chunk(0.70, "network", "C", BASE + "c.md"), chunk(0.60, "network", "D", BASE + "d.md")]
    assert [h.title for h in run(docs, top_k=3)] == ["A", "B", "C"]
    assert [h.title for h in run(docs, top_k=2)] == ["A", "B"]


def test_duplicates_are_counted_and_the_other_drops_are_unchanged() -> None:
    before = {r: sample("incident_memory_dropped_total", reason=r)
              for r in ("duplicate", "score", "category")}
    run(netpol_chunks() + [chunk(0.40, "network", "weak", BASE + "w.md"),
                           chunk(0.70, "storage", "other", BASE + "s.md")])
    after = {r: sample("incident_memory_dropped_total", reason=r) for r in before}
    assert (after["duplicate"] - before["duplicate"], after["score"] - before["score"],
            after["category"] - before["category"]) == (3, 1, 1)


def test_a_weak_chunk_of_a_kept_document_is_a_score_drop_not_a_duplicate() -> None:
    before = sample("incident_memory_dropped_total", reason="duplicate")
    hits = run([chunk(0.9, "network", "A", BASE + "a.md"),
                chunk(0.4, "network", "A", BASE + "a.md")])
    assert [h.score for h in hits] == [0.9]
    assert sample("incident_memory_dropped_total", reason="duplicate") == before


def test_two_documents_with_the_same_title_stay_separate() -> None:
    hits = run([chunk(0.8, "network", "Same title", BASE + "x.md"),
                chunk(0.7, "network", "Same title", BASE + "y.md")])
    assert [h.source for h in hits] == [BASE + "x.md", BASE + "y.md"]


def test_without_a_source_the_title_identifies_the_document() -> None:
    hits = run([chunk(0.8, "network", "Titled", None), chunk(0.7, "network", "Titled", None),
                chunk(0.6, "network", "Other", None)])
    assert [(h.title, h.score) for h in hits] == [("Titled", 0.8), ("Other", 0.6)]


def test_chunks_with_neither_source_nor_title_are_never_merged() -> None:
    nameless = []
    for score in (0.8, 0.7, 0.6):
        item = chunk(score, "network", "x", None)
        item["metadata"].pop("title")           # and no "# heading" in the chunk text
        item["content"]["text"] = f"just text {score}"
        nameless.append(item)
    assert [h.score for h in run(nameless)] == [0.8, 0.7, 0.6]


def test_a_learned_document_is_deduplicated_like_any_other() -> None:
    uri = BASE + "learned/inc-1.md"
    hits = run([chunk(0.8, "network", "LRN-inc-1", uri), chunk(0.75, "network", "LRN-inc-1", uri)])
    assert len(hits) == 1 and hits[0].origin == "learned" and hits[0].score == 0.8


def test_unfiltered_queries_are_deduplicated_too() -> None:
    results = [chunk(0.8, "storage", "A", BASE + "a.md"), chunk(0.7, "storage", "A", BASE + "a.md")]
    assert len(run(results, category=None)) == 1


@pytest.mark.parametrize("source,title,expected", [
    (BASE + "a.md", "T", BASE + "a.md"), ("", "T", "title:T"),
    ("", "Untitled postmortem", "chunk:4")])
def test_document_key(source: str, title: str, expected: str) -> None:
    item = chunk(0.7, "network", title, source or None)
    assert document_key(to_similar(item), 4) == expected
