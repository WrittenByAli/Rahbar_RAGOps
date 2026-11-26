"""A small per-request record of what the query pipeline did.

Its keys are a subset of the trace record in docs/a3/CONTRACTS.md. For now the record only
lives in memory for the length of the request: nothing writes it to stdout or to disk, so a
trace_id cannot be looked up yet. The full trace is later work (Phase 3 of the plan).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from .config import Settings
from .schemas import Citation, Passage, RetrievedRef

TraceRecord = dict[str, Any]


def new_trace(question: str, settings: Settings) -> TraceRecord:
    """Start the record for one request. answer_question() fills in the rest."""
    return {
        "kind": "trace",
        "schema": 1,
        "trace_id": uuid.uuid4().hex,
        "query": question,
        # version is the collection's index generation; None means it could not be read
        "collection": {"name": settings.collection, "version": None},
        "context": [],           # chunks sent to the model; stays empty if it was not called
        "citations_raw": [],     # exactly what the model wrote
        "citations": [],         # what the user received
        "cache": {"status": "disabled", "source_trace_id": None},
        "release_id": settings.release_id,
    }


def valid_citations(
    citations: Sequence[Citation], sent: Sequence[Passage | RetrievedRef | Citation]
) -> list[Citation]:
    """The citations that name a chunk the model was given, with that chunk's page.

    A citation matches on source and section, exactly. The prompt shows no page numbers, so a
    page written by the model is a guess: it is neither compared nor kept. The page comes from
    the first matching chunk in `sent`, which is ordered best first. The objects passed in are
    not changed, so the raw citations stay as the model wrote them.
    """
    pages: dict[tuple[str, str], int | None] = {}
    for chunk in sent:
        pages.setdefault((chunk.source, chunk.section), chunk.page)
    return [
        c.model_copy(update={"page": pages[(c.source, c.section)]})
        for c in citations
        if (c.source, c.section) in pages
    ]


def citation_validity(
    raw: Sequence[Citation], sent: Sequence[Passage | RetrievedRef | Citation]
) -> float | None:
    """Share of the model's raw citations that name a chunk sent to it.

    Pass the raw list (citations_raw). The filtered list scores 1.0 by construction.
    None means the model cited nothing: there is no measurement, so it is neither 1.0 nor 0.0.
    """
    if not raw:
        return None
    return len(valid_citations(raw, sent)) / len(raw)


def citation_validity_from_record(record: TraceRecord) -> float | None:
    """citation_validity() computed from a trace record and nothing else.

    None for a cache hit or an abstention: the model was not called, so it cited nothing.
    """
    raw = [Citation(**c) for c in record["citations_raw"]]
    # A context entry names its chunk the way a citation does: source, section and page.
    sent = [
        Citation(source=c["source"], section=c["section"], page=c["page"])
        for c in record["context"]
    ]
    return citation_validity(raw, sent)
