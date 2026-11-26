"""Requirement D3: a script summarises the trace log over a time window.

Every expected number below is worked out by hand in the comment next to it.
"""

from __future__ import annotations

import json
import re

import pytest
import trace_summary
from trace_samples import cache_hit, candidate, make_record, no_model_call, write_lines

SINCE = "2026-11-26T10:00:00Z"
UNTIL = "2026-11-26T11:00:00Z"
REQUIRED_KEYS = (
    "top1_similarity",
    "abstention_rate",
    "cache_hit_rate",
    "latency_ms_p95_by_stage",
    "injection_flagged",
)


def run_summary(tmp_path, rows, since=SINCE, until=UNTIL) -> dict:
    """Run the real command with --out and read back the one file it wrote."""
    traces = write_lines(tmp_path / "in" / "t.jsonl", rows)
    out_dir = tmp_path / "out"
    code = trace_summary.main(
        ["--traces", str(traces), "--since", since, "--until", until, "--out", str(out_dir)]
    )
    assert code == 0
    (written,) = out_dir.glob("*.json")
    return json.loads(written.read_text(encoding="utf-8"))


def scored(trace_id: str, score: float, **changes) -> dict:
    return make_record(trace_id, retrieved=[candidate("chunk-a", score, 1)], **changes)


def test_window_filters_records(tmp_path):
    # The window is since <= ts < until. Each record has its own score, so the
    # smallest and largest score show which records were counted.
    rows = [
        scored("before", 0.01, ts="2026-11-26T09:59:59Z"),            # out: too early
        scored("at-since", 0.30, ts="2026-11-26T10:00:00Z"),          # in: since is included
        scored("middle", 0.50, ts="2026-11-26T10:30:00.500000Z"),     # in
        scored("offset", 0.70, ts="2026-11-26T15:45:00+05:00"),       # in: 10:45:00Z
        scored("at-until", 0.98, ts="2026-11-26T11:00:00Z"),          # out: until is excluded
        scored("after", 0.99, ts="2026-11-26T12:00:00Z"),             # out: too late
        scored("not-a-time", 0.97, ts="yesterday"),                   # skipped
        scored("no-zone", 0.96, ts="2026-11-26T10:10:00"),            # skipped: zone unknown
        scored("no-time", 0.95, ts=None),                             # skipped
    ]

    summary = run_summary(tmp_path, rows)

    assert summary["records"] == 3
    assert summary["skipped"]["outside_window"] == 3
    assert summary["skipped"]["bad_timestamp"] == 3
    assert summary["top1_similarity"]["count"] == 3
    assert summary["top1_similarity"]["min"] == 0.30
    assert summary["top1_similarity"]["max"] == 0.70
    assert summary["window"]["since"] == SINCE
    assert summary["window"]["until"] == UNTIL


def test_window_bounds_may_use_another_zone(tmp_path):
    rows = [scored("in", 0.5, ts="2026-11-26T10:30:00Z")]

    summary = run_summary(
        tmp_path, rows, since="2026-11-26T15:00:00+05:00", until="2026-11-26T16:00:00+05:00"
    )

    assert summary["records"] == 1
    assert summary["window"]["since"] == SINCE and summary["window"]["until"] == UNTIL


def test_nearest_rank_hand_computed():
    # Values 15 20 35 40 50. Rank = ceil(percent/100 * 5), counting from 1.
    values = [40, 15, 50, 20, 35]
    assert trace_summary.nearest_rank(values, 30) == 20    # ceil(1.5)  = 2nd
    assert trace_summary.nearest_rank(values, 40) == 20    # ceil(2.0)  = 2nd
    assert trace_summary.nearest_rank(values, 50) == 35    # ceil(2.5)  = 3rd
    assert trace_summary.nearest_rank(values, 95) == 50    # ceil(4.75) = 5th
    assert trace_summary.nearest_rank(list(range(1, 101)), 95) == 95
    assert trace_summary.nearest_rank([8], 95) == 8
    assert trace_summary.nearest_rank([], 95) is None


def latency(cache, embed, retrieve, guardrail, prompt, generate, total) -> dict:
    return {
        "cache": cache,
        "embed": embed,
        "retrieve": retrieve,
        "guardrail": guardrail,
        "prompt": prompt,
        "generate": generate,
        "total": total,
    }


def test_p95_per_stage_hand_computed(tmp_path):
    # 20 model calls, i = 1..20. The cache lookup takes 1 ms, except 5 ms (i=19) and 7 ms (i=20).
    calls = [
        make_record(
            f"call-{i}",
            latency_ms=latency(
                {19: 5, 20: 7}.get(i, 1), i, 2 * i, 0, 3, 10 * i, 100 * i
            ),
        )
        for i in range(20, 0, -1)  # newest first: the input order must not matter
    ]
    # 1 abstention below the relevance floor: it retrieved, but never built or sent a prompt.
    abstention = no_model_call("abstain", latency_ms=latency(1, 50, 60, 0, 0, 0, 120))
    # 20 cache hits: only the cache lookup ran. Their zeros are not measurements.
    hits = [
        cache_hit(f"hit-{i}", "call-1", latency_ms=latency(2, 0, 0, 0, 0, 0, 4))
        for i in range(20)
    ]

    summary = run_summary(tmp_path, [*calls, abstention, *hits])

    # Nearest rank: the value at position ceil(0.95 * n) of the sorted values.
    assert summary["latency_n_by_stage"] == {
        "cache": 41,      # every record looked in the cache
        "embed": 21,      # 20 calls + the abstention
        "retrieve": 21,
        "guardrail": 21,
        "prompt": 20,     # only records with a prompt
        "generate": 20,
        "total": 41,
    }
    assert summary["latency_ms_p95_by_stage"] == {
        # sorted: nineteen 1s, twenty 2s, 5, 7. ceil(38.95) = 39th value = 2
        "cache": 2,
        # 1..20 and 50. ceil(19.95) = 20th value = 20
        "embed": 20,
        # 2, 4, ..., 40 and 60. 20th value = 40
        "retrieve": 40,
        # twenty-one real zeros: the rail ran and took under a millisecond
        "guardrail": 0,
        "prompt": 3,
        # 10..200. ceil(19.0) = 19th value = 190 (interpolation would give 190.5, and counting
        # the 21 zeros of the records that never called the model would give 180)
        "generate": 190,
        # twenty 4s, 100, 120, 200, ..., 2000. 39th value = 1800
        "total": 1800,
    }
    assert summary["percentile_method"] == "nearest-rank"


def test_stage_times_that_are_null_or_absent_are_not_counted(tmp_path):
    rows = [
        make_record("a", latency_ms={"total": 10, "generate": None}),
        make_record("b", latency_ms={"total": 30}),
    ]

    summary = run_summary(tmp_path, rows)

    assert summary["latency_ms_p95_by_stage"]["total"] == 30
    assert summary["latency_ms_p95_by_stage"]["generate"] is None
    assert summary["latency_n_by_stage"]["generate"] == 0


def test_top1_similarity_distribution_hand_computed(tmp_path):
    rows = [scored(f"t{i}", score) for i, score in enumerate([0.9, 0.2, 0.6, 0.5])]
    # Only the first candidate is the top-1, even when the relevance floor later drops it.
    rows[1]["retrieved"] = [
        candidate("low", 0.2, 1, dropped_by="relevance_floor"),
        candidate("lower", 0.1, 2, dropped_by="relevance_floor"),
    ]
    rows.append(cache_hit("hit", "t0"))                 # no retrieval: not counted
    # Even a hit that carries a copy of its source's candidates retrieved nothing itself.
    rows.append(cache_hit("hit-copy", "t0", retrieved=[candidate("chunk-a", 0.99, 1)]))
    rows.append(make_record("nothing", retrieved=[]))   # nothing retrieved: not counted

    top1 = run_summary(tmp_path, rows)["top1_similarity"]

    assert top1["count"] == 4
    assert top1["min"] == 0.2
    assert top1["p50"] == 0.5                           # 0.2 0.5 0.6 0.9: ceil(2.0) = 2nd
    assert top1["mean"] == pytest.approx(0.55)          # 2.2 / 4
    assert top1["max"] == 0.9
    filled = {(b["from"], b["to"]): b["count"] for b in top1["histogram"] if b["count"]}
    assert filled == {(0.2, 0.3): 1, (0.5, 0.6): 1, (0.6, 0.7): 1, (0.9, 1.0): 1}


def test_histogram_edges():
    bins = trace_summary.histogram([-0.2, 0.0, 0.7, 1.0])
    counts = {(b["from"], b["to"]): b["count"] for b in bins}
    assert counts[(-1.0, 0.0)] == 1    # below zero has its own bin
    assert counts[(0.0, 0.1)] == 1
    assert counts[(0.7, 0.8)] == 1     # a lower edge belongs to the bin above it
    assert counts[(0.9, 1.0)] == 1     # 1.0 belongs to the last bin
    assert sum(counts.values()) == 4


def test_abstention_rate_hand_computed(tmp_path):
    failed = make_record("error")
    failed["response"] = {**failed["response"], "answered_from_context": None}
    rows = [
        make_record("answered-1"),
        cache_hit("answered-2", "answered-1"),
        no_model_call("declined"),
        failed,  # no answer was produced, so it is neither answered nor declined
    ]

    summary = run_summary(tmp_path, rows)

    assert summary["abstention"] == {"abstained": 1, "n": 3}
    assert summary["abstention_rate"] == pytest.approx(1 / 3)


def with_status(trace_id: str, status: str) -> dict:
    if status == "hit":
        return cache_hit(trace_id, "source")
    return make_record(trace_id, cache={"status": status, "source_trace_id": None})


def test_cache_hit_rate_counts_only_hits_and_misses(tmp_path):
    statuses = ["hit", "miss", "miss", "miss", "bypass", "bypass", "disabled", "disabled"]
    rows = [with_status(f"t{i}", status) for i, status in enumerate(statuses)]

    summary = run_summary(tmp_path, rows)

    assert summary["cache_hit_rate"] == 0.25   # 1 hit / (1 hit + 3 misses), not 1 / 8
    assert summary["cache"]["n"] == 4
    assert summary["cache"]["status_counts"] == {"hit": 1, "miss": 3, "bypass": 2, "disabled": 2}


def test_cache_hit_rate_is_null_when_the_cache_was_never_consulted(tmp_path):
    rows = [with_status("a", "bypass"), with_status("b", "disabled")]

    summary = run_summary(tmp_path, rows)

    assert summary["cache_hit_rate"] is None
    assert summary["latency_ms_p95_by_stage"]["total"] is not None


def action(rail: str, name: str, chunk_id: str | None = None) -> dict:
    return {"rail": rail, "action": name, "rule_id": "r1", "chunk_id": chunk_id, "doc_id": "d"}


def test_injection_flags_are_counted_by_rail_and_action(tmp_path):
    rows = [
        make_record(
            "two-chunks",
            guardrail_actions=[
                action("retrieval.injection", "drop_chunk", "c1"),
                action("retrieval.injection", "drop_chunk", "c2"),
            ],
        ),
        make_record("query", guardrail_actions=[action("input.injection", "flag")]),
        make_record("other-rail", guardrail_actions=[action("output.citation", "strip")]),
        make_record("clean"),
    ]

    flagged = run_summary(tmp_path, rows)["injection_flagged"]

    assert flagged == {
        "total": 3,
        "records_flagged": 2,
        "by_rail_action": {
            "retrieval.injection": {"drop_chunk": 2},
            "input.injection": {"flag": 1},
        },
        "other_guardrail_actions": 1,
    }


def test_no_flags_in_a_window_with_records_is_a_real_zero(tmp_path):
    flagged = run_summary(tmp_path, [make_record("clean")])["injection_flagged"]

    assert flagged["total"] == 0 and flagged["by_rail_action"] == {}


def test_junk_lines_are_skipped_and_counted(tmp_path):
    record = make_record("good")
    anonymous = make_record("unused")
    del anonymous["trace_id"]
    rows = [
        "",                                                                      # blank: ignored
        "INFO:     Uvicorn running on http://0.0.0.0:8000",                      # not JSON
        "{not json",                                                             # not JSON
        '{"ts": "2026-11-26T10:00:00Z", "level": "INFO", "msg": "answered"}',    # not a trace
        '["an", "array"]',                                                       # not a trace
        "42",                                                                    # not a trace
        json.dumps(make_record("cut"))[:150],                                    # broken trace
        anonymous,                                                               # no trace id
        record,
        record,  # the same line again, as from stdout and from the volume: counted once
    ]

    summary = run_summary(tmp_path, rows)

    assert summary["records"] == 1
    assert summary["skipped"] == {
        "not_json": 2,
        "not_a_trace": 3,
        "broken_trace_line": 1,
        "no_trace_id": 1,
        "duplicate": 1,
        "bad_timestamp": 0,
        "outside_window": 0,
    }


def test_odd_values_inside_a_record_do_not_crash_or_count(tmp_path):
    odd = make_record(
        "odd",
        retrieved=[{"score": None}],
        response=None,
        cache=None,
        latency_ms=None,
        guardrail_actions=None,
    )

    summary = run_summary(tmp_path, [odd])

    assert summary["records"] == 1
    for key in ("top1_similarity", "abstention_rate", "cache_hit_rate", "latency_ms_p95_by_stage"):
        assert summary[key] is None, key


def test_empty_window_gives_nulls_never_zero_or_one(tmp_path, capsys):
    rows = [make_record("outside", ts="2026-11-25T10:00:00Z")]

    summary = run_summary(tmp_path, rows)

    assert summary["records"] == 0
    assert summary["skipped"]["outside_window"] == 1
    for key in REQUIRED_KEYS:
        assert key in summary and summary[key] is None, key
    captured = capsys.readouterr()
    assert "no records in this window" in captured.err
    assert "n/a" in captured.out


def test_result_file_has_a_stamped_name_and_the_contract_keys(tmp_path):
    summary = run_summary(tmp_path, [make_record("a")])

    (written,) = (tmp_path / "out").glob("*.json")
    assert re.fullmatch(r"\d{8}T\d{6}Z-window\.json", written.name)
    assert summary["window"]["since"] and summary["window"]["until"]
    for key in REQUIRED_KEYS:
        assert summary[key] is not None, key


def test_label_names_the_result_file(tmp_path):
    traces = write_lines(tmp_path / "t.jsonl", [make_record("a")])
    args = ["--traces", str(traces), "--since", SINCE, "--until", UNTIL]

    assert trace_summary.main([*args, "--out", str(tmp_path / "out"), "--label", "demo"]) == 0

    (written,) = (tmp_path / "out").glob("*.json")
    assert written.name.endswith("Z-demo.json")


def test_a_repeated_traces_option_adds_files_and_drops_none(tmp_path, capsys):
    one = write_lines(tmp_path / "one.jsonl", [make_record("a")])
    two = write_lines(tmp_path / "two.jsonl", [make_record("b")])
    args = ["--traces", str(one), "--traces", str(two), "--since", SINCE, "--until", UNTIL]

    assert trace_summary.main(args) == 0

    assert "records  2 in the window" in capsys.readouterr().out


def test_nothing_is_written_without_out(tmp_path, monkeypatch, capsys):
    traces = write_lines(tmp_path / "t.jsonl", [make_record("a")])
    monkeypatch.chdir(tmp_path)
    before = sorted(tmp_path.rglob("*"))

    code = trace_summary.main(["--traces", str(traces), "--since", SINCE, "--until", UNTIL])

    assert code == 0
    assert sorted(tmp_path.rglob("*")) == before
    table = capsys.readouterr().out
    for heading in ("top-1 similarity", "abstention rate", "cache hit rate", "p95", "injection"):
        assert heading in table, heading


@pytest.mark.parametrize(
    "changes",
    [
        {"--since": "2026-11-26T10:00:00"},   # no zone
        {"--since": "last tuesday"},
        {"--since": UNTIL, "--until": SINCE},  # ends before it starts
        {"--label": "../escape"},
        {"--traces": "no-such-directory"},
    ],
)
def test_bad_arguments_are_input_errors_and_write_nothing(tmp_path, changes):
    traces = write_lines(tmp_path / "t.jsonl", [make_record("a")])
    args = {"--traces": str(traces), "--since": SINCE, "--until": UNTIL}
    args["--out"] = str(tmp_path / "out")
    args.update(changes)

    code = trace_summary.main([part for pair in args.items() for part in pair])

    assert code == trace_summary.EXIT_BAD_INPUT
    assert not (tmp_path / "out").exists()


def test_two_different_records_with_one_id_stop_the_summary(tmp_path, capsys):
    traces = write_lines(tmp_path / "t.jsonl", [make_record("a"), make_record("a", query="x?")])
    out_dir = tmp_path / "out"

    code = trace_summary.main(
        ["--traces", str(traces), "--since", SINCE, "--until", UNTIL, "--out", str(out_dir)]
    )

    assert code == trace_summary.EXIT_INCONSISTENT
    assert "two different records" in capsys.readouterr().err
    assert not out_dir.exists()
