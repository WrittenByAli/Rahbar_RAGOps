"""What changed between two suite results, for the data drift report (E3).

    python eval/drift.py --before <S0.json> --after <S1.json>

Lists the metric deltas and every question whose outcome, answer or retrieval changed. The
script does not guess why. Each change has "cause": null until a person states it. Do not
edit the result file: write the causes in your own JSON file and run again with --causes:

    {"g003": {"type": "data" or "model" or "software", "cause": "... (trace evidence)"}}

Both runs must use the same golden set, seed and judge (model, digest, prompt and seed).
The report says whether the release and the corpus state were the same. For data drift the
release is the same and the corpus state differs.
Exit code 0, or 2 when the two results cannot be compared.
"""

from __future__ import annotations

import argparse
import os
import sys

from suite.common import (
    EVAL_DIR,
    JUDGE_SETTINGS,
    MODULE_METRICS,
    SUITE_METRICS,
    SuiteError,
    get,
    load_json,
    require_same,
    stamp,
    use_utf8_output,
    utc_now,
    write_new_json,
)
from suite.metrics import difference

CAUSE_TYPES = ("data", "model", "software")
COMPARED = (*MODULE_METRICS, *SUITE_METRICS, "end_to_end.answerable_answer_rate",
            "end_to_end.latency_ms_p50_answered", "end_to_end.latency_ms_p95_answered",
            "component.corpus_a_subset.recall_at_3", "component.corpus_a_subset.recall_at_5",
            "component.corpus_a_subset.mrr", "component.corpus_a_subset.ndcg_at_5")


def _observed(question: dict) -> dict:
    return {
        "answered_from_context": question.get("answered_from_context"),
        "error": question.get("error"),
        "judge_verdict": get(question, "judge.verdict"),
        "recall_at_3": get(question, "metrics.recall_at_3"),
        "recall_at_5": get(question, "metrics.recall_at_5"),
        "reciprocal_rank": get(question, "metrics.reciprocal_rank"),
        "ndcg_at_5": get(question, "metrics.ndcg_at_5"),
        # Which pages of which document versions came back, in order. Scores are left out:
        # they move whenever the corpus does.
        "top5": [[c.get("doc_id"), c.get("doc_version"), c.get("page_start"),
                  c.get("chunk_id")] for c in question.get("top5") or []],
        "answer": question.get("answer"),
    }


def question_changes(before: dict, after: dict) -> list[dict]:
    first = {q["id"]: q for q in before.get("questions") or []}
    second = {q["id"]: q for q in after.get("questions") or []}
    if not first or set(first) != set(second):
        raise SuiteError("the two results do not list the same questions")
    rows = []
    for qid, old in first.items():
        was, now = _observed(old), _observed(second[qid])
        changes = {name: {"before": was[name], "after": now[name]}
                   for name in was if was[name] != now[name]}
        if changes:
            rows.append({"id": qid, "question": old.get("question"),
                         "category": old.get("category"),
                         "before_trace_id": old.get("trace_id"),
                         "after_trace_id": second[qid].get("trace_id"),
                         "changes": changes, "cause_type": None, "cause": None})
    return rows


def build_report(before: dict, after: dict, causes: dict) -> dict:
    if not before.get("run_id") or before.get("run_id") == after.get("run_id"):
        raise SuiteError("two different runs are needed")
    if not before.get("golden_sha256") or before["golden_sha256"] != after.get("golden_sha256"):
        raise SuiteError("the golden set differs between the two runs")
    require_same(before, after, JUDGE_SETTINGS)
    for key in ("release_id", "corpus_state_id"):
        if not (before.get(key) and after.get(key)):
            raise SuiteError(f"'{key}' is missing")
    changes = question_changes(before, after)
    unknown = set(causes) - {row["id"] for row in changes}
    if unknown:
        raise SuiteError(f"causes given for questions that did not change: {sorted(unknown)}")
    for row in changes:
        if row["id"] in causes:
            entry = causes[row["id"]]
            if not (isinstance(entry, dict) and entry.get("type") in CAUSE_TYPES
                    and str(entry.get("cause") or "").strip()):
                raise SuiteError(f"cause of {row['id']}: needs a type {CAUSE_TYPES} "
                                 "and a cause")
            row.update(cause_type=entry["type"], cause=str(entry["cause"]).strip())
    return {
        "before_run_id": before["run_id"],
        "after_run_id": after["run_id"],
        "golden_sha256": before["golden_sha256"],
        "same_release": before["release_id"] == after["release_id"],
        "same_corpus_state": before["corpus_state_id"] == after["corpus_state_id"],
        **{f"{side}_{key}": run.get(key)
           for side, run in (("before", before), ("after", after))
           for key in ("corpus_state_id", "release_id", "git_sha")},
        "metric_deltas": {name: {"before": get(before, name), "after": get(after, name),
                                 "delta": difference(get(after, name), get(before, name))}
                          for name in COMPARED},
        "per_question_changes": changes,
        "causes_missing": [row["id"] for row in changes if row["cause"] is None],
    }


def main(argv: list[str] | None = None) -> int:
    use_utf8_output()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--causes", help="JSON file written by you")
    parser.add_argument("--out", default=os.environ.get(
        "DRIFT_OUT", str(EVAL_DIR / "results" / "drift")), help="[DRIFT_OUT]")
    args = parser.parse_args(argv)
    try:
        report = build_report(load_json(args.before), load_json(args.after),
                              load_json(args.causes) if args.causes else {})
        now = utc_now()
        path = write_new_json(args.out, f"{stamp(now)}-drift", report)
    except SuiteError as exc:
        print(f"drift: {exc}", file=sys.stderr)
        return 2
    print(f"same release: {report['same_release']}, "
          f"same corpus state: {report['same_corpus_state']}")
    for name, row in report["metric_deltas"].items():
        if row["delta"] not in (None, 0):
            print(f"{name}: {row['before']:.4g} -> {row['after']:.4g}")
    for row in report["per_question_changes"]:
        print(f"{row['id']}: {', '.join(row['changes'])}  "
              f"[cause: {row['cause'] or 'not stated'}]")
    print(f"causes still to be stated: {len(report['causes_missing'])}")
    print(f"result: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
