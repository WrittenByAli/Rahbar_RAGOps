"""Compare a baseline and a candidate suite result at all three levels (C1).

    python eval/compare.py --baseline <suite.json> --candidate <suite.json> \
        --change "<the one change, in your words>"

Exit code 0: the comparison was written. 2: the two runs cannot be compared (golden set or
corpus state differ) or something is missing. It decides nothing: that is eval/gate.py.
"""

from __future__ import annotations

import argparse
import os
import sys

from gate import check_comparable
from suite.common import (
    EVAL_DIR,
    MODULE_METRICS,
    SUITE_METRICS,
    SuiteError,
    get,
    label_part,
    load_json,
    stamp,
    use_utf8_output,
    utc_now,
    write_new_json,
)
from suite.metrics import difference

LEVELS = ("module", "component", "end_to_end")
COMPARED = (*MODULE_METRICS, *SUITE_METRICS, "end_to_end.answerable_answer_rate",
            "end_to_end.latency_ms_p50_answered", "end_to_end.latency_ms_p95_answered")


def build_comparison(baseline: dict, candidate: dict, change: str) -> dict:
    check_comparable(baseline, candidate)
    if not change.strip():
        raise SuiteError("--change must describe the one change")

    def side(result: dict) -> dict:
        missing = [level for level in LEVELS if not result.get(level)]
        if missing:
            raise SuiteError(f"{result['run_id']}: level {', '.join(missing)} is missing")
        keys = ("run_id", "release_id", "git_sha", "seed", "golden_sha256",
                "corpus_state_id", "config", *LEVELS)
        return {key: result.get(key) for key in keys}

    return {
        "change": change.strip(),
        "baseline": side(baseline),
        "candidate": side(candidate),
        "deltas": {name: {"baseline": get(baseline, name), "candidate": get(candidate, name),
                          "delta": difference(get(candidate, name), get(baseline, name))}
                   for name in COMPARED},
        "delta_definition": "candidate minus baseline",
    }


def main(argv: list[str] | None = None) -> int:
    use_utf8_output()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--change", required=True, help="written by you, not generated")
    parser.add_argument("--out", default=os.environ.get(
        "COMPARE_OUT", str(EVAL_DIR / "results" / "compare")), help="[COMPARE_OUT]")
    args = parser.parse_args(argv)
    try:
        report = build_comparison(load_json(args.baseline), load_json(args.candidate),
                                  args.change)
        now = utc_now()
        name = f"{stamp(now)}-{label_part(report['candidate']['release_id'])}"
        path = write_new_json(args.out, name, report)
    except SuiteError as exc:
        print(f"compare: {exc}", file=sys.stderr)
        return 2
    print(f"{'metric':<38} {'baseline':>10} {'candidate':>10} {'delta':>10}")
    for metric, row in report["deltas"].items():
        cells = ["null" if row[k] is None else f"{row[k]:.4g}"
                 for k in ("baseline", "candidate", "delta")]
        print(f"{metric:<38} {cells[0]:>10} {cells[1]:>10} {cells[2]:>10}")
    print(f"result: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
