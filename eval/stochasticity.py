"""Compare two runs of the suite with the same seed, metric by metric (B.S).

    python eval/stochasticity.py --run1 <suite.json> --run2 <suite.json>
        prints the differences and writes nothing, so you can look into the causes;
    ... --explanation "<what you found>"     (or --explanation-file <file>)
        also writes the result file.

The explanation is written by a person. This script does not suggest causes.
Exit code 0, or 2 if the two runs are not two runs of the same setup.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from suite.common import (
    EVAL_DIR,
    MODULE_METRICS,
    SUITE_METRICS,
    SuiteError,
    get,
    load_json,
    stamp,
    use_utf8_output,
    utc_now,
    write_new_json,
)
from suite.metrics import difference

# The check wants at least this many characters; a shorter text is rarely an explanation.
MIN_EXPLANATION = 80
SAME = ("seed", "golden_sha256", "corpus_state_id", "release_id")


def changed_questions(run1: dict, run2: dict) -> list[dict]:
    """The questions whose answer, outcome or retrieval differed, to start looking from."""
    second = {q["id"]: q for q in run2.get("questions") or []}
    changed = []
    for before in run1.get("questions") or []:
        after = second.get(before["id"], {})
        fields = {
            "answer_sha256": (before.get("answer_sha256"), after.get("answer_sha256")),
            "answered_from_context": (before.get("answered_from_context"),
                                      after.get("answered_from_context")),
            "judge_verdict": (get(before, "judge.verdict"), get(after, "judge.verdict")),
            "error": (before.get("error"), after.get("error")),
            "retrieval_metrics": (before.get("metrics"), after.get("metrics")),
        }
        names = [name for name, (a, b) in fields.items() if a != b]
        if names:
            changed.append({"id": before["id"], "differs": names,
                            "trace_ids": [before.get("trace_id"), after.get("trace_id")]})
    return changed


def compare_runs(run1: dict, run2: dict) -> dict:
    if not run1.get("run_id") or run1.get("run_id") == run2.get("run_id"):
        raise SuiteError("two different runs are needed")
    for key in SAME:
        if run1.get(key) is None or run1.get(key) != run2.get(key):
            raise SuiteError(f"{key} differs or is missing: {run1.get(key)!r} and "
                             f"{run2.get(key)!r}")
    return {
        "run_ids": [run1["run_id"], run2["run_id"]],
        **{key: run1[key] for key in SAME},
        "git_shas": [run1.get("git_sha"), run2.get("git_sha")],
        "metrics": {name: {"run1": get(run1, name), "run2": get(run2, name),
                           "diff": difference(get(run2, name), get(run1, name))}
                    for name in (*SUITE_METRICS, *MODULE_METRICS,
                                 "end_to_end.answerable_answer_rate")},
        "diff_definition": "run2 minus run1; null if either value is null",
        "changed_questions": changed_questions(run1, run2),
    }


def main(argv: list[str] | None = None) -> int:
    use_utf8_output()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run1", required=True)
    parser.add_argument("--run2", required=True)
    text = parser.add_mutually_exclusive_group()
    text.add_argument("--explanation", help="written by you")
    text.add_argument("--explanation-file", help="a text file written by you")
    parser.add_argument("--out", default=os.environ.get(
        "STOCHASTICITY_OUT", str(EVAL_DIR / "results" / "stochasticity")))
    args = parser.parse_args(argv)
    try:
        report = compare_runs(load_json(args.run1), load_json(args.run2))
        print(f"{'metric':<38} {'run1':>10} {'run2':>10} {'diff':>10}")
        for name, row in report["metrics"].items():
            cells = ["null" if row[k] is None else f"{row[k]:.4g}"
                     for k in ("run1", "run2", "diff")]
            print(f"{name:<38} {cells[0]:>10} {cells[1]:>10} {cells[2]:>10}")
        for row in report["changed_questions"]:
            print(f"changed {row['id']}: {', '.join(row['differs'])}  "
                  f"traces {row['trace_ids']}")
        explanation = args.explanation
        if args.explanation_file:
            try:
                explanation = Path(args.explanation_file).read_text(encoding="utf-8-sig")
            except (OSError, ValueError) as exc:
                raise SuiteError(f"cannot read {args.explanation_file}: {exc}") from exc
        if explanation is None:
            print("no explanation given: nothing written")
            return 0
        explanation = explanation.strip()
        if len(explanation) < MIN_EXPLANATION:
            raise SuiteError(f"the explanation needs at least {MIN_EXPLANATION} characters")
        now = utc_now()
        path = write_new_json(args.out, f"{stamp(now)}-seed{report['seed']}",
                              {**report, "explanation": explanation})
    except SuiteError as exc:
        print(f"stochasticity: {exc}", file=sys.stderr)
        return 2
    print(f"result: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
