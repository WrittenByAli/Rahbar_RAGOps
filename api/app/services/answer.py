"""The full query pipeline (section 3.2-3.4)."""

from __future__ import annotations

import logging

from ..config import Settings
from ..guardrails import RETRIEVAL_RAIL, apply_retrieval_rail, inspect_question
from ..logjson import request_id_var
from ..model_versions import embedding_version
from ..prompting import build_prompt
from ..providers.llm.base import LLMProvider, run_provider
from ..retrieval.base import Retriever
from ..schemas import AskResponse, Passage, RetrievedRef, RetrieveResponse
from ..trace import (
    StageClock,
    TraceRecord,
    candidate_entries,
    context_entry,
    finish_trace,
    mark_dropped,
    new_trace,
    prompt_record,
    valid_citations,
)
from .cache import AnswerCache

log = logging.getLogger("rahbar.answer")

ABSTENTION = "I don't have enough information in my knowledge base to answer this question."


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


def _collection_state(retriever: Retriever, settings: Settings) -> dict[str, str | None]:
    """"version" and "lake_state_id" of the collection. A retriever without a store (the
    stub) has no lake state, and None for both means the state could not be read."""
    read = getattr(retriever, "collection_state", None)
    if settings.retriever == "stub" or read is None:
        return {"version": _index_generation(retriever, settings), "lake_state_id": None}
    try:
        return read()
    except Exception as exc:  # noqa: BLE001 - retrieval itself reports a broken store
        log.warning("collection state unavailable", extra={"error": str(exc)})
        return {"version": None, "lake_state_id": None}


def _cache_lookup(
    question: str, settings: Settings, generation: str | None
) -> tuple[AnswerCache | None, AskResponse | None]:
    """The cache to use (None: do not use it) and the stored answer, if there is one."""
    if generation is None:
        return None, None
    try:
        cache = AnswerCache(settings, index_generation=generation)
        cached = cache.get(question)
        if cached is not None and not cached.trace_id:
            cached = None      # without its source trace, a hit's prompt could not be shown
        return cache, cached
    except Exception as exc:  # noqa: BLE001 - a cache failure must never stop answering
        log.warning("cache unavailable", extra={"error": str(exc)})
        return None, None


def _store_filters(retriever: Retriever) -> list[dict[str, object]]:
    """The filters the retriever itself applies in its query (the stub applies none)."""
    return [{"name": name, "value": value}
            for name, value in getattr(retriever, "filters", {}).items()]


def _retrieve(retriever: Retriever, question: str, k: int, clock: StageClock) -> list[Passage]:
    """Retrieve, timing the embedding and the search apart if the retriever has both steps."""
    embed = getattr(retriever, "embed_query", None)
    search = getattr(retriever, "search", None)
    if embed is None or search is None:
        with clock.stage("retrieve"):
            return retriever.retrieve(question, k=k)
    with clock.stage("embed"):
        vector = embed(question)
    with clock.stage("retrieve"):
        return search(vector, k)


def answer_question(
    question: str,
    retriever: Retriever,
    provider: LLMProvider,
    settings: Settings,
    *,
    bypass_cache: bool = False,
    trace: TraceRecord | None = None,
) -> AskResponse:
    """Answer one question and leave one trace line, however the request ends.

    To read the record afterwards, pass one from new_trace().
    """
    record = trace if trace is not None else new_trace(question, settings)
    clock = StageClock()
    try:
        response = _run_pipeline(
            question, retriever, provider, settings, record, clock, bypass_cache
        )
        # A fallback text sent because the model call failed is not an abstention. The API
        # response still says false; in the trace, null means that no answer was produced.
        record["response"].update(
            text=response.answer,
            answered_from_context=None if "error" in record else response.answered_from_context,
            confidence=response.confidence,
        )
        return response
    except Exception as exc:
        # The type only: an error message can quote a URL, a header or a document.
        record["error"] = {"stage": clock.failed, "type": type(exc).__name__}
        raise
    finally:
        finish_trace(record, clock, settings)


def _run_pipeline(
    question: str,
    retriever: Retriever,
    provider: LLMProvider,
    settings: Settings,
    record: TraceRecord,
    clock: StageClock,
    bypass_cache: bool,
) -> AskResponse:
    request_id = request_id_var.get()

    # Step 0: Input rail. It only flags, and it runs before the cache so hits are checked too.
    with clock.stage("guardrail"):
        record["guardrail_actions"] += inspect_question(question)

    # Step 1: Check the exact-match cache (never used in stub mode). `cache` stays None when
    # it must not be used, which also prevents the write in step 7.
    if settings.retriever == "stub" or not settings.cache_enabled:
        record["cache"]["status"] = "disabled"
    elif bypass_cache:
        record["cache"]["status"] = "bypass"
    else:
        record["cache"]["status"] = "miss"      # also when the cache cannot be reached

    # The index generation is read before retrieval: if the index changes during this
    # request, the answer is stored under the old generation, which is no longer read.
    cache = None
    if record["cache"]["status"] == "miss":
        with clock.stage("cache"):
            generation = _index_generation(retriever, settings)
            cache, cached = _cache_lookup(question, settings, generation)
        record["collection"]["version"] = generation
        if cached is not None:
            record["cache"] = {"status": "hit", "source_trace_id": cached.trace_id}
            record["citations"] = [c.model_dump() for c in cached.citations]
            log.info("cache hit", extra={"question": question[:60]})
            # Both ids are this request's, not those of the request that was cached.
            return cached.model_copy(
                update={"request_id": request_id, "trace_id": record["trace_id"],
                        "trace": None}
            )
    else:
        record["collection"]["version"] = _index_generation(retriever, settings)

    # Step 2: Retrieve passages. The trace keeps every candidate, whatever happens next.
    record["filters_applied"] = _store_filters(retriever)
    passages = _retrieve(retriever, question, settings.top_k, clock)
    record["retrieved"] = candidate_entries(passages)

    # Step 3: Apply relevance floor
    below = [p.chunk_id for p in passages if not p.score >= settings.relevance_floor]
    passages = [p for p in passages if p.chunk_id not in below]
    mark_dropped(record["retrieved"], below, "relevance_floor")
    record["filters_applied"].append(
        {"name": "relevance_floor", "value": settings.relevance_floor, "dropped": below})

    # Step 4: Retrieval rail, on the chunks that could reach the prompt
    removed: list[str] = []
    if settings.retrieval_rail_enabled:
        with clock.stage("guardrail"):
            passages, actions = apply_retrieval_rail(passages)
        record["guardrail_actions"] += actions
        removed = [a["chunk_id"] for a in actions if a["action"] == "drop_chunk"]
        mark_dropped(record["retrieved"], removed, RETRIEVAL_RAIL)
    # Recorded even when off, or "off" and "found nothing" would look the same in a trace.
    record["filters_applied"].append(
        {"name": "retrieval_rail", "value": settings.retrieval_rail_enabled,
         "dropped": removed})

    # Step 5: If 0 passages remain -> abstention
    if not passages:
        return AskResponse(
            request_id=request_id,
            answer=ABSTENTION,
            citations=[],
            answered_from_context=False,
            confidence="low",
            retrieved=[],
            provider=provider.name,
            trace_id=record["trace_id"],
        )

    # Step 6: Build the prompt and generate. Both are recorded before the model is called, so
    # a failed call still shows what was about to be sent.
    with clock.stage("prompt"):
        prompt = build_prompt(question, passages, settings.prompt_version)
        record["prompt"] = prompt_record(prompt)
        record["context"] = [context_entry(p) for p in passages]
    record["reranked_order"] = [p.chunk_id for p in passages]    # no reranker: as retrieved
    with clock.stage("generate"):
        result = run_provider(provider, question, passages, prompt)
    answer = result.answer
    record["llm"]["attempt"] = result.attempt
    record["llm"]["prompt_eval_count"] = result.prompt_eval_count
    if result.seed is not None:
        record["llm"]["params"]["seed"] = result.seed
    record["response"]["raw_output"] = result.raw_output
    if result.failure:
        record["error"] = {"stage": "generate", "type": result.failure}

    # Step 7: Validate citations — strip any not sent to the model, and take each page from
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
            "passages_sent": len(passages),
            "chunk_ids": [p.chunk_id for p in passages],
            "scores": [round(p.score, 4) for p in passages],
            "answered_from_context": answer.answered_from_context,
            "citations_valid": len(kept),
            "citations_total": len(answer.citations),
            "trace_id": record["trace_id"],
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

    # Step 8: Store in cache (unless abstention)
    if cache and answer.answered_from_context:
        with clock.stage("cache"):
            try:
                cache.put(question, resp)
            except Exception as exc:  # noqa: BLE001 - best-effort, must never break answering
                log.warning("cache store failed", extra={"error": str(exc)})

    return resp


def retrieve_candidates(
    question: str, retriever: Retriever, settings: Settings, k: int
) -> RetrieveResponse:
    """Retrieval on its own: ranked candidates, marked as the relevance floor would treat them.

    No model, cache, rail, trace line or query metric.
    """
    state = _collection_state(retriever, settings)
    passages = retriever.retrieve(question, k=k)
    candidates = candidate_entries(passages)
    below = [p.chunk_id for p in passages if not p.score >= settings.relevance_floor]
    mark_dropped(candidates, below, "relevance_floor")
    return RetrieveResponse(
        request_id=request_id_var.get(),
        release_id=settings.release_id,
        collection={"name": settings.collection, **state},
        embedding={"model": settings.embed_model, "version": embedding_version(settings)},
        top_k=k,
        filters_applied=[
            *_store_filters(retriever),
            {"name": "relevance_floor", "value": settings.relevance_floor, "dropped": below},
        ],
        retrieved=candidates,
    )
