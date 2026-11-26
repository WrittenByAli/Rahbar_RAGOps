"""The contracts. Change them only with your partner's review: the UI, the
evaluation script and your tests all depend on these shapes.

`Answer` is what the model must produce (section 3.4). `AskResponse` is what
the API returns to the UI and to eval/run_eval.py. The model's `reasoning` is
never sent to the user.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    # The two flags below were added later: docs/adr/0007-api-additive-fields.md
    bypass_cache: bool = Field(
        default=False, description="Neither read nor write the answer cache for this request."
    )
    include_trace: bool = Field(
        default=False,
        description="Return this request's trace record, which holds the full prompt. Ignored "
        "unless the server sets EVAL_DEBUG_ENABLED=true.",
    )


class Passage(BaseModel):
    """One retrieved chunk, as the retriever hands it to the generator."""

    chunk_id: str
    text: str
    source: str
    section: str
    page: int | None = None
    score: float = Field(description="Similarity, higher is closer (1 - cosine distance).")
    # Document identity, and the pages of the chunk's own words, as the store's metadata gives
    # them. None means the store did not say: nothing is guessed.
    doc_id: str | None = None
    doc_version: str | None = None
    page_start: int | None = None
    page_end: int | None = None


class Citation(BaseModel):
    source: str
    section: str
    page: int | None = None


class Answer(BaseModel):
    """The model's output contract. Send its JSON schema to the model, then validate."""

    reasoning: str
    answer: str
    citations: list[Citation]
    answered_from_context: bool
    confidence: Literal["high", "medium", "low"]


class RetrievedRef(BaseModel):
    """What the response says was retrieved. eval/run_eval.py scores against this."""

    chunk_id: str
    source: str
    section: str
    page: int | None = None
    score: float


class AskResponse(BaseModel):
    request_id: str
    answer: str
    citations: list[Citation]
    answered_from_context: bool
    confidence: Literal["high", "medium", "low"]
    retrieved: list[RetrievedRef]
    provider: str
    # Id of this request's trace line (app/trace.py).
    trace_id: str | None = None
    # The trace record itself: only when the request asks and the server allows it.
    trace: dict[str, Any] | None = None


class RetrieveRequest(BaseModel):
    """POST /api/retrieve: retrieval on its own, for component tests."""

    question: str = Field(min_length=3, max_length=500)
    k: int | None = Field(default=None, ge=1, le=50, description="List depth. Default: TOP_K.")


class RetrieveResponse(BaseModel):
    request_id: str
    release_id: str
    # name, version (index generation), lake_state_id (None: not recorded or being written)
    collection: dict[str, str | None]
    embedding: dict[str, str | None]        # model, version (digest)
    top_k: int
    filters_applied: list[dict[str, Any]]
    # Ranked best first, before the relevance floor. Each entry has the same keys as an entry
    # of retrieved[] in a trace record. The retrieval rail does not run here.
    retrieved: list[dict[str, Any]]
