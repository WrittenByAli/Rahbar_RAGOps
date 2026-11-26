"""How often the faithfulness judge agrees with a person (B.E2).

Step 1, label blind. This prints question, answer and context, and never a verdict. Do not
open the suite result file before you have labelled: it holds the verdicts.

    python eval/judge_agreement.py show --suite <suite.json>

Step 2. A member writes eval/judge_labels.jsonl by hand, one line per answer:
    {"trace_id": "...", "answer_sha256": "...", "human_label": "supported" or
     "unsupported", "labeller": "...", "labelled_at": "..."}
No script creates or edits that file.

Step 3, score:
    python eval/judge_agreement.py score --suite <suite.json>

A judge verdict "error" never agrees. Exit code 0, or 2 when a label cannot be matched.
"""

from __future__ import annotations

import argparse
import os
import random
import sys

from suite.common import (
    EVAL_DIR,
    SuiteError,
    get,
    label_part,
    load_json,
    load_jsonl,
    stamp,
    use_utf8_output,
    utc_now,
    write_new_json,
)

LABELS = ("supported", "unsupported")
LABEL_KEYS = ("trace_id", "answer_sha256", "human_label", "labeller", "labelled_at")


def judged_answers(suite: dict) -> list[dict]:
    """The answers the judge was asked about: answered answerable questions."""
    return [q for q in suite.get("questions") or [] if q.get("judge")]


def blind_view(suite: dict, seed: int = 0) -> str:
    """The answers and their context, in shuffled order, with nothing from the judge."""
    answers = judged_answers(suite)
    random.Random(seed).shuffle(answers)
    blocks = [f"{len(answers)} answers from run {suite.get('run_id')}. Is every claim of "
              "the answer supported by the context shown with it?"]
    # Only the fields named here are printed, so a verdict cannot slip through.
    for number, row in enumerate(answers, 1):
        context = "\n".join(
            f"  [{i}] {c.get('source')} | {c.get('section')} | page {c.get('page')}\n"
            f"  {c.get('text')}" for i, c in enumerate(row.get("context") or [], 1))
        blocks.append(
            f"===== {number} of {len(answers)} =====\n"
            f"trace_id: {row.get('trace_id')}\nanswer_sha256: {row.get('answer_sha256')}\n"
            f"QUESTION: {row.get('question')}\nANSWER: {row.get('answer')}\n"
            f"CONTEXT:\n{context}")
    return "\n\n".join(blocks)


def score(suites: list[dict], labels: list[dict]) -> dict:
    answers = {row["trace_id"]: (row, suite) for suite in suites
               for row in judged_answers(suite)}
    compared, seen = [], set()
    for label in labels:
        trace_id = label["trace_id"]
        if label["human_label"] not in LABELS:
            raise SuiteError(f"label {trace_id}: human_label must be one of {LABELS}")
        if trace_id in seen:
            raise SuiteError(f"label {trace_id}: this answer is labelled twice")
        seen.add(trace_id)
        if trace_id not in answers:
            raise SuiteError(f"label {trace_id}: no judged answer with this trace_id in the "
                             "given suite results")
        row, suite = answers[trace_id]
        if label["answer_sha256"] != row["answer_sha256"]:
            raise SuiteError(f"label {trace_id}: answer_sha256 does not match the answer")
        verdict = row["judge"]["verdict"]
        compared.append({"trace_id": trace_id, "question_id": row["id"],
                         "run_id": suite["run_id"], "labeller": label["labeller"],
                         "human_label": label["human_label"], "judge_verdict": verdict,
                         "agree": label["human_label"] == verdict})
    models = {get(suite, "config.judge.model") for _, suite in
              (answers[c["trace_id"]] for c in compared)}
    if len(models) != 1 or None in models:
        raise SuiteError(f"the labelled answers need one known judge model, got {models}")
    agreements = sum(1 for c in compared if c["agree"])
    return {
        "labelled": len(compared),
        "agreements": agreements,
        "agreement_rate": agreements / len(compared),
        "judge_model": models.pop(),
        "judge_errors": sum(1 for c in compared if c["judge_verdict"] == "error"),
        "run_ids": sorted({c["run_id"] for c in compared}),
        "comparisons": compared,
    }


def main(argv: list[str] | None = None) -> int:
    use_utf8_output()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("show", help="print answers for blind labelling")
    show.add_argument("--suite", required=True)
    show.add_argument("--shuffle-seed", type=int, default=0)
    scoring = commands.add_parser("score", help="compare the hand labels with the judge")
    scoring.add_argument("--suite", required=True, action="append",
                         help="may be given more than once")
    scoring.add_argument("--labels", default=str(EVAL_DIR / "judge_labels.jsonl"))
    scoring.add_argument("--out", default=os.environ.get(
        "JUDGE_AGREEMENT_OUT", str(EVAL_DIR / "results" / "judge_agreement")))
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            print(blind_view(load_json(args.suite), args.shuffle_seed))
            return 0
        report = score([load_json(path) for path in args.suite],
                       load_jsonl(args.labels, required=LABEL_KEYS))
        now = utc_now()
        path = write_new_json(args.out, f"{stamp(now)}-{label_part(report['judge_model'])}",
                              report)
    except SuiteError as exc:
        print(f"judge agreement: {exc}", file=sys.stderr)
        return 2
    print(f"judge {report['judge_model']} agreed with the hand labels on "
          f"{report['agreements']} of {report['labelled']} answers "
          f"({report['agreement_rate']:.0%})\nresult: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
