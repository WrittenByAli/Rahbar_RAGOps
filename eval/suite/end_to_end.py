"""End to end: POST /api/ask with the cache bypassed, scored from the returned traces.

All questions are asked first and judged afterwards, so a judge model that has to be
swapped into memory cannot add to the measured latency.
"""

from __future__ import annotations

import time

import httpx

from . import metrics
from .common import SuiteError, post_json, sha256_text
from .judge import judge_answer

# An in-domain question that is in no golden file and not in live_queries.jsonl. It has to
# reach the model: a question that is declined at the relevance floor leaves the LLM
# unloaded, and the first measured request would then pay for loading it.
WARMUP_QUESTION = "What is the minimum CGPA an undergraduate student needs to be awarded a degree?"


def ask(client: httpx.Client, target: str, question: str) -> dict:
    return post_json(client, f"{target}/api/ask",
                     {"question": question, "bypass_cache": True, "include_trace": True})


def warm_up(client: httpx.Client, target: str) -> None:
    """One request that is not measured. It must call the model, and it also shows early
    that traces are returned."""
    try:
        reply = ask(client, target, WARMUP_QUESTION)
    except (httpx.HTTPError, ValueError) as exc:
        raise SuiteError(f"warm-up request to {target}/api/ask failed: {exc}") from exc
    trace = reply.get("trace")
    if not trace:
        raise SuiteError("the response has no trace: the API under test must run with "
                         "EVAL_DEBUG_ENABLED=true")
    if trace.get("error") or not trace.get("prompt"):
        reason = (f"it failed ({trace['error']})" if trace.get("error")
                  else "no prompt was sent: it was declined before the model")
        raise SuiteError(f"the warm-up request did not warm up the model, because {reason}. "
                         "Check the index and the model before measuring.")


def ask_question(client: httpx.Client, target: str, question: dict) -> dict:
    """One golden question. A failed request, or a trace with "error", produced no answer:
    its answered_from_context is None, so it is never counted as an abstention."""
    row = {"id": question["id"], "category": question["category"],
           "question": question["question"], "trace": None,
           "answered_from_context": None, "error": None, "client_ms": None}
    start = time.perf_counter()
    try:
        reply = ask(client, target, question["question"])
    except (httpx.HTTPError, ValueError) as exc:
        row["error"] = {"stage": "request", "type": type(exc).__name__}
        return row
    row["client_ms"] = (time.perf_counter() - start) * 1000
    trace = reply.get("trace")
    if not trace:
        raise SuiteError(f"question {question['id']}: the response has no trace")
    if trace["cache"]["status"] not in ("bypass", "disabled"):
        raise SuiteError(f"question {question['id']}: cache status "
                         f"'{trace['cache']['status']}', but the cache must be bypassed")
    row.update(trace=trace, error=trace.get("error"))
    if not row["error"]:
        answered = trace["response"]["answered_from_context"]
        if isinstance(answered, bool):
            row["answered_from_context"] = answered
        else:
            row["error"] = {"stage": "trace", "type": "no_answered_from_context"}
    return row


def question_record(row: dict) -> dict:
    """What the result file keeps for one question."""
    trace = row["trace"] or {}
    answer = (trace.get("response") or {}).get("text")
    raw = trace.get("citations_raw") or []
    context = trace.get("context") or []
    return {
        "id": row["id"],
        "category": row["category"],
        "question": row["question"],
        "trace_id": trace.get("trace_id"),
        "error": row["error"],
        "answered_from_context": row["answered_from_context"],
        "answer": answer,
        "answer_sha256": None if answer is None else sha256_text(answer),
        "context": context,
        "citations_raw": raw,
        "citations_raw_valid": metrics.valid_citation_count(raw, context),
        "latency_ms": (trace.get("latency_ms") or {}).get("total"),
        "client_latency_ms": row["client_ms"],
        "judge": None,
    }


def judge_records(client: httpx.Client, ollama_url: str, model: str, seed: int,
                  records: list[dict]) -> None:
    """Judge every answer given to an answerable question."""
    for record in records:
        if record["category"] == "answerable" and record["answered_from_context"] is True:
            record["judge"] = judge_answer(client, ollama_url, model, seed,
                                           record["question"], record["answer"],
                                           record["context"])


def summarise(records: list[dict]) -> dict:
    """The end-to-end metrics, computed from the question records alone."""
    answerable = [r for r in records if r["category"] == "answerable"]
    unanswerable = [r for r in records if r["category"] == "unanswerable"]
    answers = [r for r in answerable if r["answered_from_context"] is True]
    supported = sum(1 for r in answers if r["judge"] and r["judge"]["verdict"] == "supported")
    judge_errors = sum(1 for r in answers if r["judge"] and r["judge"]["verdict"] == "error")
    completed = [r for r in records if not r["error"]]
    latencies = [r["latency_ms"] for r in completed if r["latency_ms"] is not None]
    answered_latencies = [r["latency_ms"] for r in completed
                          if r["answered_from_context"] is True and r["latency_ms"] is not None]
    raw = sum(len(r["citations_raw"]) for r in completed)
    valid = sum(r["citations_raw_valid"] for r in completed)

    def share_of(group: list[dict], answered: bool) -> float | None:
        # An error is neither an abstention nor an answer, so it counts only in the
        # denominator. If every request of the group failed there is no measurement.
        if not any(not r["error"] for r in group):
            return None
        return metrics.ratio(
            sum(1 for r in group if r["answered_from_context"] is answered), len(group))

    judged = len(answers) > judge_errors     # at least one usable verdict
    return {
        # A judge error stays in the denominator: it is never counted as supported.
        "faithfulness": metrics.ratio(supported, len(answers)) if judged else None,
        "faithfulness_n": len(answers),
        "faithfulness_supported": supported,
        "faithfulness_judge_errors": judge_errors,
        "abstention_correctness": share_of(unanswerable, False),
        "abstention_n": len(unanswerable),
        "citation_validity": metrics.ratio(valid, raw),
        "citation_n": raw,
        "latency_ms_p50": metrics.percentile(latencies, 50),
        "latency_ms_p95": metrics.percentile(latencies, 95),
        "latency_n": len(latencies),
        # Declined questions can skip the model and are then much faster, so the latency
        # of the answered requests is reported on its own as well.
        "latency_ms_p50_answered": metrics.percentile(answered_latencies, 50),
        "latency_ms_p95_answered": metrics.percentile(answered_latencies, 95),
        "latency_answered_n": len(answered_latencies),
        "latency_method": "nearest rank over the trace's latency_ms.total; cache bypassed; "
                          "one warm-up request excluded; failed requests excluded",
        "answerable_answer_rate": share_of(answerable, True),
        "answerable_n": len(answerable),
        "errors": len(records) - len(completed),
    }
