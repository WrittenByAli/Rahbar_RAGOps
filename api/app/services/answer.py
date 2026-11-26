"""The full query pipeline (section 3.2-3.4)."""

from __future__ import annotations

import logging

from ..config import Settings
from ..logjson import request_id_var
from ..prompting import evidence_text
from ..providers.llm.base import LLMProvider
from ..retrieval.base import Retriever
from ..schemas import AskResponse, RetrievedRef
from ..trace import TraceRecord, new_trace, valid_citations

log = logging.getLogger("rahbar.answer")


def _index_generation(retriever: Retriever, settings: Settings) -> str | None:
    """The current index generation, or None when it cannot be read.

    None means "do not use the cache for this request". Assuming "0" instead could return an
    answer that was cached before a re-index.
    """
    if settings.retriever == "stub":
        return "0"
    try:
        return retriever.index_generation()
    except Exception as exc:  # noqa: BLE001 - cache is best-effort, must never break answering
        log.warning("index generation unavailable", extra={"error": str(exc)})
        return None


def answer_question(
    question: str,
    retriever: Retriever,
    provider: LLMProvider,
    settings: Settings,
    *,
    bypass_cache: bool = False,
    trace: TraceRecord | None = None,
) -> AskResponse:
    """Answer one question. To read the record afterwards, pass one from new_trace()."""
    request_id = request_id_var.get()
    record = trace if trace is not None else new_trace(question, settings)
    # Read before retrieval: if the index changes during this request, the answer is stored
    # under the old generation, which later requests no longer read.
    generation = _index_generation(retriever, settings)
    record["collection"]["version"] = generation

    # Step 1: Check the exact-match cache (never used in stub mode). `cache` stays None when
    # it must not be used, which also prevents the write in step 7.
    cache = None
    if settings.retriever == "stub" or not settings.cache_enabled:
        record["cache"]["status"] = "disabled"
    elif bypass_cache:
        record["cache"]["status"] = "bypass"
    else:
        record["cache"]["status"] = "miss"      # also when the cache cannot be reached
        if generation is not None:
            try:
                from .cache import AnswerCache
                cache = AnswerCache(settings, index_generation=generation)
                cached = cache.get(question)
                if cached is not None:
                    record["cache"] = {"status": "hit", "source_trace_id": cached.trace_id}
                    record["citations"] = [c.model_dump() for c in cached.citations]
                    log.info("cache hit", extra={"question": question[:60]})
                    # Both ids are this request's, not those of the request that was cached.
                    return cached.model_copy(
                        update={"request_id": request_id, "trace_id": record["trace_id"]}
                    )
            except Exception as exc:  # noqa: BLE001 - a cache failure must never stop answering
                log.warning("cache unavailable", extra={"error": str(exc)})

    # Step 2: Retrieve passages
    passages = retriever.retrieve(question, k=settings.top_k)

    # Step 3: Apply relevance floor
    passages = [p for p in passages if p.score >= settings.relevance_floor]

    # Step 4: If 0 passages remain -> abstention
    if not passages:
        return AskResponse(
            request_id=request_id,
            answer="I don't have enough information in my knowledge base to answer this question.",
            citations=[],
            answered_from_context=False,
            confidence="low",
            retrieved=[],
            provider=provider.name,
            trace_id=record["trace_id"],
        )

    # Step 5: Generate answer
    # source and section are stored as retrieved: they are what citations are matched against.
    record["context"] = [
        {"chunk_id": p.chunk_id, "source": p.source, "section": p.section, "page": p.page,
         "text": evidence_text(p.text)}
        for p in passages
    ]
    answer = provider.generate(question, passages)

    # Step 6: Validate citations — strip any not sent to the model, and take each page from
    # the chunk. Keep both lists: validity is measured on the raw one.
    kept = valid_citations(answer.citations, passages)
    record["citations_raw"] = [c.model_dump() for c in answer.citations]
    record["citations"] = [c.model_dump() for c in kept]

    log.info(
        "answered",
        extra={
            "retriever": retriever.name,
            "provider": provider.name,
            "top_k": settings.top_k,
            "passages_after_floor": len(passages),
            "chunk_ids": [p.chunk_id for p in passages],
            "scores": [round(p.score, 4) for p in passages],
            "answered_from_context": answer.answered_from_context,
            "citations_valid": len(kept),
            "citations_total": len(answer.citations),
        },
    )

    resp = AskResponse(
        request_id=request_id,
        answer=answer.answer,
        citations=kept,
        answered_from_context=answer.answered_from_context,
        confidence=answer.confidence,
        retrieved=[
            RetrievedRef(
                chunk_id=p.chunk_id, source=p.source, section=p.section, page=p.page, score=p.score
            )
            for p in passages
        ],
        provider=provider.name,
        trace_id=record["trace_id"],
    )

    # Step 7: Store in cache (unless abstention)
    if cache and answer.answered_from_context:
        try:
            cache.put(question, resp)
        except Exception as exc:  # noqa: BLE001 - cache is best-effort, must never break answering
            log.warning("cache store failed", extra={"error": str(exc)})

    # Step 8: Return
    return resp
