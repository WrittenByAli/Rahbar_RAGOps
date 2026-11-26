"""Summarise the trace log over a time window (requirement D3).

    python eval/trace_summary.py --traces PATH... --since ISO --until ISO
                                 [--out DIR] [--label NAME]

Times are ISO-8601 with a zone, e.g. 2026-11-26T10:00:00Z. The window is since <= ts < until,
so two windows that share an edge never count a record twice. A table is always printed. JSON
is written only with --out, as DIR/<UTC stamp>-<label>.json.

What each number counts:
  top-1 similarity  score of retrieved[0], taken before the relevance floor. Cache hits are
                    left out: they retrieve nothing.
  abstention rate   answered_from_context false / records where it is true or false. A record
                    where it is null (no answer was produced) is not counted.
  cache hit rate    hit / (hit + miss). bypass and disabled never consult the cache.
  latency p95       nearest rank: the value at position ceil(0.95 * n) of the sorted times.
                    A time above 0 always counts. A 0 counts only if the record shows that
                    the stage ran (see stage_ran). A null or absent time never counts.
  injection flags   guardrail actions whose rail name contains "injection".

A value with nothing to compute it from is null, never 0 or 1.
Exit codes: 0 done, 2 bad arguments or input, 6 two different records share a trace id.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tracelib import (
    SKIP_REASONS,
    LoadedTraces,
    TraceInputError,
    as_dict,
    as_list,
    cache_status,
    format_time,
    load_traces,
    parse_time,
    use_utf8_output,
)

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_INCONSISTENT = 6

STAGES = ("cache", "embed", "retrieve", "guardrail", "prompt", "generate", "total")
LOOKUP_STATUSES = ("hit", "miss")  # the denominator of the cache hit rate


def number(value: Any) -> float | None:
    """The value if it is a finite number. True and False are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value if math.isfinite(value) else None


def rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def nearest_rank(values: list[float], percent: int) -> float | None:
    """The value at position ceil(percent / 100 * n), counting from 1, of the sorted values."""
    if not values:
        return None
    rank = max(1, (percent * len(values) + 99) // 100)  # whole numbers: no float rounding
    return sorted(values)[rank - 1]


def select_window(
    records: list[dict], since: datetime, until: datetime
) -> tuple[list[dict], Counter]:
    inside: list[dict] = []
    left_out: Counter = Counter()
    for record in records:
        try:
            when = parse_time(record.get("ts"))
        except ValueError:
            left_out["bad_timestamp"] += 1
            continue
        if since <= when < until:
            inside.append(record)
        else:
            left_out["outside_window"] += 1
    return inside, left_out


# ---------------------------------------------------------------- the five measurements
def histogram(scores: list[float]) -> list[dict]:
    """One bin below zero, then ten bins 0.1 wide. A bin includes its lower edge; the last
    one also includes 1.0."""
    bins = [{"from": -1.0, "to": 0.0, "count": 0}]
    bins += [{"from": i / 10, "to": (i + 1) / 10, "count": 0} for i in range(10)]
    for score in scores:
        index = 0 if score < 0 else 1 + min(int(score * 10), 9)
        bins[index]["count"] += 1
    return bins


def top1_similarity(records: list[dict]) -> dict | None:
    scores = []
    for record in records:
        retrieved = as_list(record.get("retrieved"))
        if cache_status(record) == "hit" or not retrieved:
            continue
        score = number(as_dict(retrieved[0]).get("score"))
        if score is not None:
            scores.append(score)
    if not scores:
        return None
    return {
        "count": len(scores),
        "min": min(scores),
        "p50": nearest_rank(scores, 50),
        "mean": sum(scores) / len(scores),
        "max": max(scores),
        "histogram": histogram(scores),
    }


def abstention_counts(records: list[dict]) -> dict:
    flags = [as_dict(r.get("response")).get("answered_from_context") for r in records]
    answered = [flag for flag in flags if isinstance(flag, bool)]
    return {"abstained": answered.count(False), "n": len(answered)}


def cache_counts(records: list[dict]) -> dict:
    statuses = Counter(str(cache_status(r) or "unknown") for r in records)
    return {
        "status_counts": dict(statuses),
        "n": sum(statuses[status] for status in LOOKUP_STATUSES),
    }


def stage_ran(record: dict, stage: str) -> bool:
    """Does the record show that this stage ran? Used only to judge a time of exactly 0,
    which can be a very fast stage or a placeholder for a stage that was skipped."""
    if stage == "total":
        return True
    if stage == "cache":
        return cache_status(record) in LOOKUP_STATUSES
    if stage in ("prompt", "generate"):
        return record.get("prompt") is not None
    return cache_status(record) != "hit"  # embed, retrieve, guardrail


def stage_times(records: list[dict], stage: str) -> list[float]:
    times = []
    for record in records:
        value = number(as_dict(record.get("latency_ms")).get(stage))
        if value is not None and (value > 0 or stage_ran(record, stage)):
            times.append(value)
    return times


def injection_flagged(records: list[dict]) -> dict | None:
    if not records:
        return None
    by_rail: dict[str, Counter] = {}
    total = flagged_records = other = 0
    for record in records:
        found = 0
        for item in as_list(record.get("guardrail_actions")):
            rail = str(as_dict(item).get("rail"))
            if "injection" not in rail:
                other += 1
                continue
            by_rail.setdefault(rail, Counter())[str(as_dict(item).get("action"))] += 1
            found += 1
        total += found
        flagged_records += 1 if found else 0
    return {
        "total": total,
        "records_flagged": flagged_records,
        "by_rail_action": {rail: dict(actions) for rail, actions in by_rail.items()},
        "other_guardrail_actions": other,
    }


def summarise(loaded: LoadedTraces, since: datetime, until: datetime) -> dict:
    records, left_out = select_window(list(loaded.by_id.values()), since, until)
    abstention = abstention_counts(records)
    cache = cache_counts(records)
    times = {stage: stage_times(records, stage) for stage in STAGES}
    p95 = {stage: nearest_rank(values, 95) for stage, values in times.items()}
    skipped = {reason: loaded.skipped[reason] for reason in SKIP_REASONS}
    skipped.update({reason: left_out[reason] for reason in ("bad_timestamp", "outside_window")})
    return {
        "generated_at": format_time(datetime.now(UTC)),
        "window": {
            "since": format_time(since),
            "until": format_time(until),
            "rule": "since <= ts < until",
        },
        "trace_files": [path.as_posix() for path in loaded.files],
        "records": len(records),
        "skipped": skipped,
        "top1_similarity": top1_similarity(records),
        "abstention_rate": rate(abstention["abstained"], abstention["n"]),
        "abstention": abstention,
        "cache_hit_rate": rate(cache["status_counts"].get("hit", 0), cache["n"]),
        "cache": cache,
        "percentile_method": "nearest-rank",
        "latency_ms_p95_by_stage": p95 if any(v is not None for v in p95.values()) else None,
        "latency_n_by_stage": {stage: len(values) for stage, values in times.items()},
        "injection_flagged": injection_flagged(records),
    }


# ---------------------------------------------------------------- output
def decimal(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def table(summary: dict) -> str:
    window, skipped = summary["window"], summary["skipped"]
    lines = [
        "Trace summary",
        f"window   {window['since']} <= ts < {window['until']}",
        f"files    {len(summary['trace_files'])}",
        f"records  {summary['records']} in the window",
        "skipped  " + ", ".join(f"{reason} {count}" for reason, count in skipped.items()),
        "",
    ]

    top1 = summary["top1_similarity"]
    lines.append("top-1 similarity (before the relevance floor, cache hits left out)")
    if top1 is None:
        lines.append("  n/a")
    else:
        lines.append(
            f"  count {top1['count']}  "
            + "  ".join(f"{key} {decimal(top1[key])}" for key in ("min", "p50", "mean", "max"))
        )
        for row in top1["histogram"]:
            if row["from"] >= 0 or row["count"]:
                bar = "#" * math.ceil(40 * row["count"] / top1["count"])
                lines.append(f"  {row['from']:>4} to {row['to']:<4} {row['count']:>5}  {bar}")

    abstention, cache = summary["abstention"], summary["cache"]
    statuses = ", ".join(f"{name} {count}" for name, count in cache["status_counts"].items())
    lines += [
        "",
        (f"abstention rate  {decimal(summary['abstention_rate'])}  "
         f"({abstention['abstained']} of {abstention['n']} answers declined)"),
        (f"cache hit rate   {decimal(summary['cache_hit_rate'])}  "
         f"(hits / {cache['n']} lookups; only hit and miss count; seen: {statuses or 'none'})"),
        "",
        "latency p95 per stage, ms (nearest rank)",
        f"  {'stage':<10} {'n':>6} {'p95':>12}",
    ]
    p95 = summary["latency_ms_p95_by_stage"] or {}
    for stage in STAGES:
        value = p95.get(stage)
        shown = "n/a" if value is None else f"{value:g}"
        lines.append(f"  {stage:<10} {summary['latency_n_by_stage'][stage]:>6} {shown:>12}")

    flagged = summary["injection_flagged"]
    lines.append("")
    if flagged is None:
        lines.append("injection attempts flagged  n/a")
    else:
        lines.append(
            f"injection attempts flagged  {flagged['total']} in {flagged['records_flagged']} "
            f"record(s)  (other guardrail actions: {flagged['other_guardrail_actions']})"
        )
        for rail, actions in sorted(flagged["by_rail_action"].items()):
            for name, count in sorted(actions.items()):
                lines.append(f"  {rail:<24} {name:<16} {count:>5}")
    return "\n".join(lines)


def write_result(summary: dict, out_dir: Path, label: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{stamp}-{label}.json"
    with path.open("x", encoding="utf-8", newline="\n") as handle:  # "x": never overwrite
        handle.write(json.dumps(summary, indent=2) + "\n")
    return path


def main(argv: list[str] | None = None) -> int:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="Summarise the trace log over a time window.")
    parser.add_argument(
        "--traces", nargs="+", action="extend", required=True, help="files or directories"
    )
    parser.add_argument("--since", required=True, help="first time in the window, ISO-8601")
    parser.add_argument("--until", required=True, help="end of the window (not included)")
    parser.add_argument("--out", help="directory for the JSON result (nothing written without it)")
    parser.add_argument("--label", default="window", help="used in the result file name")
    args = parser.parse_args(argv)

    try:
        since, until = parse_time(args.since), parse_time(args.until)
        if since >= until:
            raise ValueError("--since must be earlier than --until")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.label):
            raise ValueError("--label may hold only letters, digits, '-' and '_'")
        loaded = load_traces(args.traces)
    except (ValueError, TraceInputError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    if loaded.conflicts:
        print(
            "ERROR: two different records share a trace id: "
            + ", ".join(sorted(loaded.conflicts)[:5]),
            file=sys.stderr,
        )
        return EXIT_INCONSISTENT

    summary = summarise(loaded, since, until)
    print(table(summary))
    if not summary["records"]:
        print("WARNING: no records in this window; every measurement is null", file=sys.stderr)
    if summary["skipped"]["broken_trace_line"]:
        print(
            "WARNING: some trace lines could not be read, so they are not in these numbers",
            file=sys.stderr,
        )
    if args.out:
        try:
            path = write_result(summary, Path(args.out), args.label)
        except OSError as exc:
            print(f"ERROR: cannot write the result: {exc}", file=sys.stderr)
            return EXIT_BAD_INPUT
        print(f"\nwritten: {path.as_posix()}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
