"""P.2: citation validity is measured on what the model wrote, not on what survived filtering.

Finding F-01: A2 removed invalid citations first and measured validity afterwards, so the
number could only be 1.0 or "no citations".

No cluster, model or network: the stub retriever and a provider with fixed citations.
"""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.retrieval.stub import StubRetriever
from app.schemas import Answer, Citation, Passage
from app.services.answer import answer_question

QUESTION = "What does the stub handbook say?"
SENT = Citation(source="stub-handbook.pdf", section="1.1 About the stub", page=1)
INVENTED = Citation(source="stub-handbook.pdf", section="9.9 No such section", page=40)
# Stub scores are 0.91, 0.84 and 0.77: a floor of 0.9 sends only the first chunk.
DROPPED_BY_HIGH_FLOOR = Citation(
    source="stub-handbook.pdf", section="1.2 What the stub returns", page=1
)

# Top-level keys of the trace record in docs/a3/CONTRACTS.md. Phase 1 fills only some of them.
CONTRACT_KEYS = {
    "kind", "schema", "trace_id", "ts", "query", "rewritten_query", "embedding", "collection",
    "retrieved", "filters_applied", "reranker", "reranked_order", "context", "prompt", "llm",
    "response", "citations_raw", "citations", "guardrail_actions", "latency_ms", "cache",
    "release_id", "git_sha", "pod",
}


class CitingProvider:
    """Answers with exactly the citations it was given."""

    name = "citing"

    def __init__(self, citations: list[Citation]) -> None:
        self.citations = citations

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        return Answer(
            reasoning="test",
            answer="An answer.",
            citations=self.citations,
            answered_from_context=True,
            confidence="high",
        )


def test_invalid_citation_lowers_validity_metric() -> None:
    from app.trace import citation_validity, new_trace

    settings = Settings()
    record = new_trace(QUESTION, settings)
    resp = answer_question(
        QUESTION, StubRetriever(), CitingProvider([SENT, INVENTED]), settings, trace=record
    )
    raw = [Citation(**c) for c in record["citations_raw"]]

    assert citation_validity(raw, resp.retrieved) == 0.5
    # The A2 mistake, for contrast: the filtered list the user sees always scores perfectly.
    assert citation_validity(resp.citations, resp.retrieved) == 1.0


def test_all_invented_citations_score_zero() -> None:
    from app.trace import citation_validity

    passages = StubRetriever().retrieve(QUESTION, k=3)
    assert citation_validity([INVENTED, INVENTED], passages) == 0.0


def test_validity_is_none_without_raw_citations() -> None:
    from app.trace import citation_validity

    passages = StubRetriever().retrieve(QUESTION, k=3)
    assert citation_validity([], passages) is None
    assert citation_validity([], []) is None


def test_citing_a_chunk_the_model_never_received_is_invalid() -> None:
    """A chunk dropped by the relevance floor was retrieved but not sent, so it cannot count."""
    from app.trace import citation_validity, new_trace

    settings = Settings(relevance_floor=0.9)
    record = new_trace(QUESTION, settings)
    resp = answer_question(
        QUESTION, StubRetriever(), CitingProvider([DROPPED_BY_HIGH_FLOOR]), settings,
        trace=record,
    )

    assert [c["chunk_id"] for c in record["context"]] == ["stub-001"]
    assert resp.citations == []
    raw = [Citation(**c) for c in record["citations_raw"]]
    assert citation_validity(raw, resp.retrieved) == 0.0


def test_trace_keeps_raw_and_filtered_citations() -> None:
    from app.trace import new_trace

    settings = Settings()
    record = new_trace(QUESTION, settings)
    resp = answer_question(
        QUESTION, StubRetriever(), CitingProvider([SENT, INVENTED]), settings, trace=record
    )

    assert record["citations_raw"] == [SENT.model_dump(), INVENTED.model_dump()]
    assert record["citations"] == [SENT.model_dump()]
    assert resp.citations == [SENT]                   # the user still never sees the invented one

    passages = StubRetriever().retrieve(QUESTION, k=settings.top_k)
    assert record["context"] == [
        {"chunk_id": p.chunk_id, "source": p.source, "section": p.section, "page": p.page,
         "text": p.text}
        for p in passages
    ]
    assert record["query"] == QUESTION
    assert record["release_id"] == "v1"
    assert record["collection"] == {"name": "rahbar", "version": "0"}   # "0": stub retriever
    assert record["cache"] == {"status": "disabled", "source_trace_id": None}
    assert resp.trace_id == record["trace_id"]
    assert set(record) <= CONTRACT_KEYS


@pytest.mark.parametrize(("cited", "floor", "expected"), [
    ([SENT, INVENTED], 0.55, 0.5),
    ([DROPPED_BY_HIGH_FLOOR], 0.9, 0.0),      # retrieved, but the floor kept it from the model
    ([], 0.55, None),                         # the model cited nothing
    ([SENT], 0.99, None),                     # abstention: the model was never called
])
def test_validity_can_be_recomputed_from_the_trace_record_alone(
    cited: list[Citation], floor: float, expected: float | None
) -> None:
    from app.trace import citation_validity_from_record, new_trace

    settings = Settings(relevance_floor=floor)
    record = new_trace(QUESTION, settings)
    answer_question(QUESTION, StubRetriever(), CitingProvider(cited), settings, trace=record)
    stored = json.loads(json.dumps(record))   # as read back from a trace line

    assert citation_validity_from_record(stored) == expected


@pytest.mark.parametrize("written", [
    f"{SENT.section}: {SENT.section}",        # section and text copied as one string
    SENT.section.upper(),
    f"{SENT.section} ",
    "1.1",
])
def test_citation_matching_stays_exact(written: str) -> None:
    from app.trace import citation_validity

    passages = StubRetriever().retrieve(QUESTION, k=3)
    near_miss = Citation(source=SENT.source, section=written, page=1)

    assert citation_validity([near_miss], passages) == 0.0


class TwoPagesRetriever(StubRetriever):
    """Best first: two chunks of one section on different pages, then a chunk with no page."""

    def retrieve(self, question: str, k: int) -> list[Passage]:
        return [
            Passage(chunk_id="best", text="a", source="handbook.pdf", section="5.4",
                    page=7, score=0.9),
            Passage(chunk_id="second", text="b", source="handbook.pdf", section="5.4",
                    page=8, score=0.8),
            Passage(chunk_id="web", text="c", source="page.html", section="Fees",
                    page=None, score=0.7),
        ]


def test_kept_citation_takes_its_page_from_the_best_ranked_sent_chunk() -> None:
    """The prompt shows no page numbers, so a page written by the model is a guess."""
    from app.trace import new_trace

    cited = [
        Citation(source="handbook.pdf", section="5.4", page=1),
        Citation(source="page.html", section="Fees", page=3),
        Citation(source="handbook.pdf", section="no such section", page=2),
    ]
    settings = Settings()
    record = new_trace(QUESTION, settings)
    resp = answer_question(
        QUESTION, TwoPagesRetriever(), CitingProvider(cited), settings, trace=record
    )

    kept = [
        {"source": "handbook.pdf", "section": "5.4", "page": 7},
        {"source": "page.html", "section": "Fees", "page": None},
    ]
    assert [c.model_dump() for c in resp.citations] == kept
    assert record["citations"] == kept
    # The raw list is exactly what the model wrote, wrong pages included. Compared with
    # literals, so that editing the model's own objects in place would be caught.
    assert record["citations_raw"] == [
        {"source": "handbook.pdf", "section": "5.4", "page": 1},
        {"source": "page.html", "section": "Fees", "page": 3},
        {"source": "handbook.pdf", "section": "no such section", "page": 2},
    ]
    assert [c.page for c in cited] == [1, 3, 2]


def test_abstention_has_a_record_with_empty_context_and_citations() -> None:
    from app.trace import new_trace

    settings = Settings(relevance_floor=0.99)
    record = new_trace(QUESTION, settings)
    resp = answer_question(
        QUESTION, StubRetriever(), CitingProvider([SENT]), settings, trace=record
    )

    assert resp.answered_from_context is False
    assert record["context"] == []
    assert record["citations_raw"] == []
    assert record["citations"] == []
    assert resp.trace_id == record["trace_id"]


def test_every_request_gets_its_own_trace_id() -> None:
    settings = Settings()
    provider = CitingProvider([SENT])
    first = answer_question(QUESTION, StubRetriever(), provider, settings)
    second = answer_question(QUESTION, StubRetriever(), provider, settings)

    assert first.trace_id and second.trace_id
    assert first.trace_id != second.trace_id
