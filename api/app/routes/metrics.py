"""Prometheus metrics at /metrics (section 4.8).

Request count and latency are counted for every HTTP request (app/main.py). The five
rahbar_ families below it describe queries: finish_trace() updates them from the trace
record of each query, so a metric and the trace log describe the same events. POST
/api/retrieve is not a query and updates none of them.

Each pod counts on its own, and its counts start again at zero when it restarts.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from ..guardrails import INPUT_RAIL, RETRIEVAL_RAIL

router = APIRouter()

REQUESTS = Counter("rahbar_requests_total", "HTTP requests", ["path", "status"])
LATENCY = Histogram(
    "rahbar_request_seconds",
    "HTTP request latency",
    ["path"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)

TOP1_SIMILARITY = Histogram(
    "rahbar_top1_similarity",
    "Similarity of the best retrieved candidate, before the relevance floor",
    # Steps of 0.05 where the relevance floor and most scores lie.
    buckets=(0.1, 0.2, 0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0),
)
ABSTENTIONS = Counter(
    "rahbar_abstentions_total",
    "Queries answered with answered_from_context=false, by reason",
    ["reason"],
)
CACHE_REQUESTS = Counter(
    "rahbar_cache_requests_total",
    "Queries by cache outcome. Hit rate is hit / (hit + miss)",
    ["status"],
)
STAGE_LATENCY = Histogram(
    "rahbar_stage_latency_seconds",
    "Time in one pipeline stage, observed only for queries in which that stage ran",
    ["stage"],
    # From cache and rail work (milliseconds) to a slow model on a CPU (minutes).
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120),
)
INJECTION_FLAGGED = Counter(
    "rahbar_injection_flagged_total",
    "Chunks (retrieval rail) or questions (input rail) flagged as instruction-like",
    ["rail", "action"],
)

ABSTENTION_REASONS = ("no_passage_above_floor", "rail_dropped_all", "model_declined")

# A labelled metric shows no sample until its labels are first used. Create them all now, so
# every series starts at 0 and is visible before anything has happened.
for _reason in ABSTENTION_REASONS:
    ABSTENTIONS.labels(reason=_reason)
for _status in ("hit", "miss", "bypass", "disabled"):
    CACHE_REQUESTS.labels(status=_status)
for _stage in ("cache", "embed", "retrieve", "guardrail", "prompt", "generate", "total"):
    STAGE_LATENCY.labels(stage=_stage)
for _rail, _action in ((RETRIEVAL_RAIL, "redact_span"), (RETRIEVAL_RAIL, "drop_chunk"),
                       (INPUT_RAIL, "flag")):
    INJECTION_FLAGGED.labels(rail=_rail, action=_action)


def abstention_reason(record: dict[str, Any]) -> str | None:
    """Why a query was declined. None if it was answered, or if no answer was produced at
    all (an exception or a failed model call): an outage is not an abstention."""
    if record["response"]["answered_from_context"] is not False:
        return None
    if record["prompt"] is not None:
        return "model_declined"
    if any(c["dropped_by"] == RETRIEVAL_RAIL for c in record["retrieved"]):
        return "rail_dropped_all"
    return "no_passage_above_floor"


def observe_query(record: dict[str, Any], stages_ran: set[str]) -> None:
    """Count one finished query, from its trace record."""
    CACHE_REQUESTS.labels(status=record["cache"]["status"]).inc()
    if record["retrieved"]:
        TOP1_SIMILARITY.observe(record["retrieved"][0]["score"])
    reason = abstention_reason(record)
    if reason:
        ABSTENTIONS.labels(reason=reason).inc()
    for stage in stages_ran | {"total"}:
        STAGE_LATENCY.labels(stage=stage).observe(record["latency_ms"][stage] / 1000)
    for action in record["guardrail_actions"]:
        INJECTION_FLAGGED.labels(rail=action["rail"], action=action["action"]).inc()


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
