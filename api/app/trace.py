"""The trace record of one query (docs/a3/CONTRACTS.md, "Trace record").

new_trace() creates the record with every key of the contract. answer_question() fills it in
while the request runs and calls finish_trace() in a `finally` block, so answers,
abstentions, cache hits and errors each leave one JSON line: on stdout, and also in
<TRACE_DIR>/<pod name>.jsonl when TRACE_DIR is set.

A record has an "error" key {stage, type} only when no answer was produced. Its
response.answered_from_context is null, so it is never counted as an abstention.
- An exception: the caller got no answer, and response.text is "".
- A failed model call (HTTP error, or unusable output twice): response.text is the fallback
  text the caller got. The API response itself still says answered_from_context=false.

The record holds the question and the full prompt, so treat traces and logs as sensitive.
No settings object, API key or request header is ever copied into it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import socket
import sys
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .model_versions import embedding_version, llm_version
from .prompting import Prompt, evidence_text
from .routes import metrics
from .schemas import Citation, Passage, RetrievedRef

log = logging.getLogger("rahbar.trace")

TraceRecord = dict[str, Any]

# The keys of latency_ms. A stage that did not run is null, so no percentile counts it.
STAGES = ("cache", "embed", "retrieve", "guardrail", "prompt", "generate", "total")

_file_lock = threading.Lock()    # request threads of one process share one trace file


class StageClock:
    """The time one request spends in each stage, in milliseconds."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.ms = dict.fromkeys(STAGES, 0.0)
        self.ran: set[str] = set()
        self.failed: str | None = None      # the stage an exception came out of

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time one block. A stage may run twice (cache: look up, then store): times add up."""
        self.failed = None
        start = time.perf_counter()
        try:
            yield
        except BaseException:
            self.failed = name
            raise
        finally:
            self.ms[name] += (time.perf_counter() - start) * 1000
            self.ran.add(name)

    def latency_ms(self) -> dict[str, float | None]:
        """Every stage (null if it did not run) and "total", from the start of the request.

        total also covers work that belongs to no stage, such as reading model digests, so it
        is a little more than the sum of the others.
        """
        total = (time.perf_counter() - self.started) * 1000
        times = {name: round(self.ms[name], 3) if name in self.ran else None for name in STAGES}
        times["total"] = round(total, 3)
        return times


def llm_params(settings: Settings, seed: int) -> dict[str, Any]:
    """The generation settings as they are sent to the model."""
    params: dict[str, Any] = {"temperature": settings.temperature, "seed": seed}
    if settings.llm_provider.startswith("ollama"):
        params["num_ctx"] = settings.llm_num_ctx
    return params


def new_trace(question: str, settings: Settings) -> TraceRecord:
    """Start the record for one request. answer_question() fills in the rest."""
    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    return {
        "kind": "trace",
        "schema": 1,
        "trace_id": uuid.uuid4().hex,
        "ts": now.replace("+00:00", "Z"),
        "query": question,
        "rewritten_query": None,             # this pipeline does not rewrite queries
        "embedding": {"model": settings.embed_model, "version": None},
        # version is the collection's index generation; None means it could not be read
        "collection": {"name": settings.collection, "version": None},
        "retrieved": [],                     # every candidate, before any filtering
        "filters_applied": [],
        "reranker": None,                    # there is no reranker
        "reranked_order": [],                # chunk ids in the order they were sent
        "context": [],           # chunks sent to the model; stays empty if it was not called
        "prompt": None,          # stays None if the model was not called
        "llm": {"model": settings.llm_model, "version": None,
                "params": llm_params(settings, settings.llm_seed),
                "attempt": None, "prompt_eval_count": None},
        "response": {"text": "", "raw_output": None, "answered_from_context": None,
                     "confidence": None},
        "citations_raw": [],     # exactly what the model wrote
        "citations": [],         # what the user received
        "guardrail_actions": [],
        "latency_ms": dict.fromkeys(STAGES),
        "cache": {"status": "disabled", "source_trace_id": None},
        "release_id": settings.release_id,
        "git_sha": settings.git_sha or None,
        "pod": settings.pod_name or socket.gethostname(),
    }


def candidate_entries(passages: Sequence[Passage]) -> list[dict[str, Any]]:
    """One retrieved[] entry per passage, in the retriever's order. All start as kept."""
    return [
        {"chunk_id": p.chunk_id, "doc_id": p.doc_id, "doc_version": p.doc_version,
         "source": p.source, "section": p.section, "page": p.page,
         "page_start": p.page_start, "page_end": p.page_end,
         "score": p.score, "rank": rank, "kept": True, "dropped_by": None}
        for rank, p in enumerate(passages, 1)
    ]


def mark_dropped(
    candidates: list[dict[str, Any]], chunk_ids: Sequence[str], dropped_by: str
) -> None:
    for candidate in candidates:
        if candidate["chunk_id"] in chunk_ids:
            candidate["kept"] = False
            candidate["dropped_by"] = dropped_by


def context_entry(passage: Passage) -> dict[str, Any]:
    """One context[] entry. `text` is the text as the prompt shows it; source and section are
    stored as retrieved, because citations are matched against them."""
    return {"chunk_id": passage.chunk_id, "source": passage.source, "section": passage.section,
            "page": passage.page, "text": evidence_text(passage.text)}


def prompt_record(prompt: Prompt) -> dict[str, str]:
    return {
        "template_version": prompt.version,
        "sha256": hashlib.sha256(prompt.text.encode("utf-8")).hexdigest(),
        "text": prompt.text,
    }


def finish_trace(record: TraceRecord, clock: StageClock, settings: Settings) -> None:
    """Complete the record, write it and count it in the metrics.

    This runs in a `finally` block, so it must never raise: that would break a good response,
    or replace the real error. Each step is protected on its own.
    """
    try:
        record["embedding"]["version"] = embedding_version(settings)
    except Exception:
        log.exception("trace: embedding version not recorded")
    try:
        record["llm"]["version"] = llm_version(settings)
    except Exception:
        log.exception("trace: model version not recorded")
    try:
        record["latency_ms"] = clock.latency_ms()
        write_trace(record, settings)
    except Exception:
        log.exception("trace not written")
    try:
        metrics.observe_query(record, clock.ran)
    except Exception:
        log.exception("trace: metrics not updated")


def write_trace(record: TraceRecord, settings: Settings) -> None:
    """One line on stdout and, if TRACE_DIR is set, the same line in this pod's own file.

    One file for each pod, because replicas appending to a shared file could mix their lines.
    """
    # json.dumps escapes everything outside ASCII by default. That matters here: a raw U+2028
    # or U+0085 from a document would split the record for readers that use str.splitlines().
    line = json.dumps(record, separators=(",", ":")) + "\n"
    try:
        # This is not a log message: it is written whatever LOG_LEVEL is. One write() call
        # with the newline included, so another thread's log line cannot land inside it.
        sys.stdout.write(line)
        sys.stdout.flush()
    except (OSError, ValueError) as exc:
        log.warning("trace not written to stdout", extra={"error": str(exc)})

    if not settings.trace_dir:
        return
    file_name = re.sub(r"[^A-Za-z0-9_.-]", "_", record["pod"]) + ".jsonl"
    try:
        directory = Path(settings.trace_dir)
        directory.mkdir(parents=True, exist_ok=True)
        with _file_lock, open(directory / file_name, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
    except OSError as exc:
        log.warning("trace file not written", extra={"error": str(exc)})


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
