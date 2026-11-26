"""Hand-made trace records for the tests, in the shape of docs/a3/CONTRACTS.md.

They are test inputs only. Nothing here is a result of a real run, and the tests write them to
temporary directories, never to traces/.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CHUNK_A = "2.4 Academic Warning\nA student whose CGPA falls below 2.00 is placed on warning."
CHUNK_B = "2.5 Dismissal\nA student on warning for three semesters is dismissed."


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def context_entry(chunk_id: str, text: str, page: int = 7) -> dict:
    return {
        "chunk_id": chunk_id,
        "source": "handbook.pdf",
        "section": text.split("\n")[0],
        "page": page,
        "text": text,
    }


def candidate(chunk_id: str, score: float, rank: int, dropped_by: str | None = None) -> dict:
    return {
        "chunk_id": chunk_id,
        "doc_id": "ug-academic-rules",
        "doc_version": "2026-06",
        "source": "handbook.pdf",
        "section": "2.4 Academic Warning",
        "page": 7,
        "page_start": 7,
        "page_end": 8,
        "score": score,
        "rank": rank,
        "kept": dropped_by is None,
        "dropped_by": dropped_by,
    }


def prompt_for(question: str, chunks: list[dict]) -> str:
    evidence = "\n\n".join(chunk["text"] for chunk in chunks)
    return (
        "Answer ONLY from the evidence below.\n\n"
        f"<evidence>\n{evidence}\n</evidence>\n\nQuestion: {question}\n"
    )


def prompt_object(text: str) -> dict:
    return {"template_version": "p1", "sha256": sha256_of(text), "text": text}


def make_record(trace_id: str, **changes) -> dict:
    """A cache miss that called the model. Keyword arguments replace whole top-level keys."""
    question = "When is a student placed on academic warning?"
    context = [context_entry("chunk-a", CHUNK_A), context_entry("chunk-b", CHUNK_B, page=8)]
    record = {
        "kind": "trace",
        "schema": 1,
        "trace_id": trace_id,
        "ts": "2026-11-26T10:00:00Z",
        "query": question,
        "rewritten_query": None,
        "embedding": {"model": "nomic-embed-text", "version": "digest-embed"},
        "collection": {"name": "rahbar_v1", "version": "3"},
        "retrieved": [candidate("chunk-a", 0.71, 1), candidate("chunk-b", 0.63, 2)],
        "filters_applied": [{"name": "relevance_floor", "value": 0.55, "dropped": []}],
        "reranker": None,
        "reranked_order": ["chunk-a", "chunk-b"],
        "context": context,
        "prompt": prompt_object(prompt_for(question, context)),
        "llm": {
            "model": "qwen2.5:3b",
            "version": "digest-llm",
            "params": {"temperature": 0, "seed": 42},
            "attempt": 0,
        },
        "response": {
            "text": "When the CGPA falls below 2.00.",
            "raw_output": '{"answer": "When the CGPA falls below 2.00."}',
            "answered_from_context": True,
            "confidence": "high",
        },
        "citations_raw": [{"source": "handbook.pdf", "section": "2.4 Academic Warning", "page": 1}],
        "citations": [{"source": "handbook.pdf", "section": "2.4 Academic Warning", "page": 7}],
        "guardrail_actions": [],
        "latency_ms": {
            "cache": 1,
            "embed": 20,
            "retrieve": 15,
            "guardrail": 2,
            "prompt": 1,
            "generate": 900,
            "total": 940,
        },
        "cache": {"status": "miss", "source_trace_id": None},
        "release_id": "v1",
        "git_sha": "0000000",
        "pod": "api-test-pod",
    }
    record.update(changes)
    return record


def cache_hit(trace_id: str, source_trace_id: str | None, **changes) -> dict:
    """A cache hit: no retrieval, no model call, so no prompt of its own."""
    values = {
        "retrieved": [],
        "filters_applied": [],
        "reranked_order": [],
        "context": [],
        "prompt": None,
        "cache": {"status": "hit", "source_trace_id": source_trace_id},
        "latency_ms": {
            "cache": 3,
            "embed": 0,
            "retrieve": 0,
            "guardrail": 0,
            "prompt": 0,
            "generate": 0,
            "total": 4,
        },
    }
    values.update(changes)
    return make_record(trace_id, **values)


def no_model_call(trace_id: str, **changes) -> dict:
    """An abstention because every candidate fell below the relevance floor."""
    values = {
        "retrieved": [
            candidate("chunk-low-1", 0.41, 1, dropped_by="relevance_floor"),
            candidate("chunk-low-2", 0.38, 2, dropped_by="relevance_floor"),
        ],
        "filters_applied": [
            {"name": "relevance_floor", "value": 0.55, "dropped": ["chunk-low-1", "chunk-low-2"]}
        ],
        "reranked_order": [],
        "context": [],
        "prompt": None,
        "response": {
            "text": "I don't have enough information to answer this question.",
            "raw_output": None,
            "answered_from_context": False,
            "confidence": "low",
        },
        "citations_raw": [],
        "citations": [],
        "latency_ms": {
            "cache": 1,
            "embed": 20,
            "retrieve": 15,
            "guardrail": 0,
            "prompt": 0,
            "generate": 0,
            "total": 37,
        },
    }
    values.update(changes)
    return make_record(trace_id, **values)


def write_lines(path: Path, rows: list) -> Path:
    """Write one line per row. A str is written as it is (a junk line); a dict as JSON."""
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path
