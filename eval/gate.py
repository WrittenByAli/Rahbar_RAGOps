"""The release gate: apply the committed thresholds to a baseline and a candidate result.

    python eval/gate.py --baseline <suite.json> --candidate <suite.json>

Exit code 0: pass. 1: fail. 2: the comparison is invalid, or something went wrong. A result
file is written only for a pass or a fail.

The thresholds come from data."gate.yaml" of the ConfigMap file (default
k8s/gate-thresholds.yaml, the only copy). In a Job the ConfigMap key is mounted as a file,
so a file that is the gate.yaml text itself is read too. thresholds_sha256 is the hash of
the gate.yaml text, which is the same both ways.

gate.yaml must hold exactly these rules, each with a limit and a justification:

    rules:
      recall_at_5:            {limit: ..., justification: ...}  # largest drop from baseline
      faithfulness:           {limit: ..., justification: ...}  # minimum
      latency_ms_p95:         {limit: ..., justification: ...}  # maximum, in milliseconds
      abstention_correctness: {limit: ..., justification: ...}  # largest drop (0: not below)
      answerable_answer_rate: {limit: ..., justification: ...}  # minimum

A ratio limit must be from 0 to 1, and the latency limit above 0.

Guards that need no threshold:
  - no candidate metric may be null, so a pipeline that always abstains cannot pass;
  - the candidate run must have no failed request (a baseline with one is invalid);
  - the question counts and ids must match;
  - golden set, corpus state, seed, judge (model, digest, prompt, seed), retrieval list
    depth and module pairs must be present and equal, or the comparison is invalid.
The generator model and the commit may differ: they can be the change under test.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

import yaml

from suite.common import (
    EVAL_DIR,
    JUDGE_SETTINGS,
    SUITE_METRICS,
    SuiteError,
    get,
    label_part,
    load_json,
    require_same,
    sha256_text,
    stamp,
    use_utf8_output,
    utc_now,
    write_new_json,
)

DEFAULT_THRESHOLDS = EVAL_DIR.parent / "k8s" / "gate-thresholds.yaml"
EXIT_PASS, EXIT_FAIL, EXIT_INVALID = 0, 1, 2

# rule name -> (metric in the suite result, kind of limit)
RULES = {
    "recall_at_5": ("component.recall_at_5", "max_drop"),
    "faithfulness": ("end_to_end.faithfulness", "minimum"),
    "latency_ms_p95": ("end_to_end.latency_ms_p95", "maximum"),
    "abstention_correctness": ("end_to_end.abstention_correctness", "max_drop"),
    "answerable_answer_rate": ("end_to_end.answerable_answer_rate", "minimum"),
}
GUARDED_METRICS = (*SUITE_METRICS, "end_to_end.answerable_answer_rate")
# What must be equal for two runs to have been measured the same way.
SAME_SETTINGS = (*JUDGE_SETTINGS, "component.list_depth", "config.module_pairs_sha256")
COUNTS = ("component.n_questions", "end_to_end.abstention_n", "end_to_end.answerable_n")
# Rounding error only: 0.90 - 0.88 is a little more than 0.02 in floating point.
EPSILON = 1e-9


def _number(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def load_thresholds(path: str | Path) -> tuple[dict, str]:
    """(rules, sha256 of the gate.yaml text)."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
        document = yaml.safe_load(text)
        if isinstance(document, dict) and document.get("kind") == "ConfigMap":
            text = document["data"]["gate.yaml"]
            document = yaml.safe_load(text)
        rules = document["rules"]
        names = set(rules)
    except (OSError, ValueError, yaml.YAMLError, KeyError, TypeError) as exc:
        raise SuiteError(f"cannot read thresholds from {path}: {exc!r}") from exc
    if names != set(RULES):
        raise SuiteError(f"{path}: rules must be exactly {sorted(RULES)}, got {sorted(names)}")
    for name, rule in rules.items():
        if not isinstance(rule, dict) or not _number(rule.get("limit")):
            raise SuiteError(f"{path}: rule {name} needs a numeric limit")
        if not str(rule.get("justification") or "").strip():
            raise SuiteError(f"{path}: rule {name} needs a justification")
        if RULES[name][1] == "maximum":          # latency, in milliseconds
            if rule["limit"] <= 0:
                raise SuiteError(f"{path}: rule {name}: the limit must be above 0")
        elif not 0 <= rule["limit"] <= 1:        # every other metric is a ratio
            raise SuiteError(f"{path}: rule {name}: the limit must be from 0 to 1")
    return rules, sha256_text(text.replace("\r\n", "\n"))


def check_comparable(baseline: dict, candidate: dict) -> None:
    """SuiteError (exit 2) unless the two runs measured the same thing."""
    for name, result in (("baseline", baseline), ("candidate", candidate)):
        for key in ("run_id", "golden_sha256", "corpus_state_id", "release_id"):
            if not result.get(key):
                raise SuiteError(f"{name}: '{key}' is missing")
    for key in ("golden_sha256", "corpus_state_id"):
        if baseline[key] != candidate[key]:
            raise SuiteError(f"{key} differs: baseline {baseline[key]}, "
                             f"candidate {candidate[key]}")
    require_same(baseline, candidate, SAME_SETTINGS)
    if baseline["run_id"] == candidate["run_id"]:
        raise SuiteError("baseline and candidate are the same run")


def request_errors(result: dict) -> dict:
    """Failed requests of a run. None means the result does not say."""
    asked = get(result, "end_to_end.errors")
    retrieval = get(result, "component.errors")
    return {"end_to_end": asked if _number(asked) else None,
            "retrieval": len(retrieval) if isinstance(retrieval, list) else None}


NO_ERRORS = {"end_to_end": 0, "retrieval": 0}


def apply_rules(baseline: dict, candidate: dict, rules: dict) -> list[dict]:
    for name in GUARDED_METRICS:
        value = get(baseline, name)
        if not _number(value):
            raise SuiteError(f"the baseline has no usable {name} ({value!r}): "
                             "it cannot be a baseline")
    if request_errors(baseline) != NO_ERRORS:
        raise SuiteError("the baseline had failed requests, or does not say: "
                         f"{request_errors(baseline)}. It cannot be a baseline")
    for name in COUNTS:
        before, after = get(baseline, name), get(candidate, name)
        if not _number(before) or before <= 0 or before != after:
            raise SuiteError(f"question count {name} does not match: {before!r} and {after!r}")
    ids = [sorted(str(q.get("id")) for q in run.get("questions") or [])
           for run in (baseline, candidate)]
    if not ids[0] or ids[0] != ids[1]:
        raise SuiteError("the two runs do not list the same questions")

    rows = []
    nulls = [name for name in GUARDED_METRICS if not _number(get(candidate, name))]
    rows.append({"name": "no_null_metric", "baseline": None, "candidate": nulls,
                 "limit": 0, "passed": not nulls})
    errors = request_errors(candidate)
    rows.append({"name": "no_request_errors", "baseline": NO_ERRORS, "candidate": errors,
                 "limit": 0, "passed": errors == NO_ERRORS})
    for name, (metric, kind) in RULES.items():
        before, after = get(baseline, metric), get(candidate, metric)
        limit = rules[name]["limit"]
        if not _number(after):
            passed = False                       # a missing metric fails, it never passes
        elif kind == "max_drop":
            passed = before - after <= limit + EPSILON
        elif kind == "minimum":
            passed = after >= limit - EPSILON
        else:
            passed = after <= limit + EPSILON
        rows.append({"name": name, "kind": kind, "baseline": before, "candidate": after,
                     "limit": limit, "passed": passed})
    return rows


def evaluate(baseline: dict, candidate: dict, rules: dict, thresholds_sha: str) -> dict:
    check_comparable(baseline, candidate)
    rows = apply_rules(baseline, candidate, rules)
    passed = all(row["passed"] for row in rows)
    return {
        "decision": "pass" if passed else "fail",
        "exit_code": EXIT_PASS if passed else EXIT_FAIL,
        "rules": rows,
        "thresholds_sha256": thresholds_sha,
        "candidate_release": candidate["release_id"],
        "baseline_release": baseline["release_id"],
        "baseline_run_id": baseline["run_id"],
        "candidate_run_id": candidate["run_id"],
        "golden_sha256": candidate["golden_sha256"],
        "corpus_state_id": candidate["corpus_state_id"],
    }


def main(argv: list[str] | None = None) -> int:
    use_utf8_output()
    env = os.environ.get
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", default=env("GATE_BASELINE"),
                        help="suite result of the live release [GATE_BASELINE]")
    parser.add_argument("--candidate", required=True, help="suite result of the candidate")
    parser.add_argument("--thresholds",
                        default=env("GATE_THRESHOLDS", str(DEFAULT_THRESHOLDS)),
                        help="ConfigMap file or mounted gate.yaml [GATE_THRESHOLDS]")
    parser.add_argument("--out", default=env("GATE_OUT", str(EVAL_DIR / "results" / "gate")),
                        help="folder for the decision file [GATE_OUT]")
    args = parser.parse_args(argv)
    try:
        if not args.baseline:
            raise SuiteError("--baseline (or GATE_BASELINE) is required")
        rules, thresholds_sha = load_thresholds(args.thresholds)
        decision = evaluate(load_json(args.baseline), load_json(args.candidate),
                            rules, thresholds_sha)
        now = utc_now()
        decision = {"decided_at": now.isoformat(timespec="seconds"), **decision}
        name = f"{stamp(now)}-{label_part(decision['candidate_release'])}"
        path = write_new_json(args.out, name, decision)
    except SuiteError as exc:
        print(f"gate: invalid comparison or error: {exc}", file=sys.stderr)
        return EXIT_INVALID
    except Exception as exc:  # noqa: BLE001 - a crash must not look like a "fail" (exit 1)
        print(f"gate: error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INVALID
    print(f"{'rule':<24} {'baseline':>10} {'candidate':>10} {'limit':>10}  result")
    for row in decision["rules"]:
        cells = [f"{v:.4g}" if _number(v) else ("-" if v is None else str(v))
                 for v in (row["baseline"], row["candidate"], row["limit"])]
        print(f"{row['name']:<24} {cells[0]:>10} {cells[1]:>10} {cells[2]:>10}  "
              f"{'ok' if row['passed'] else 'FAILED'}")
    print(f"thresholds_sha256: {thresholds_sha}")
    print(f"decision: {decision['decision']} (candidate release "
          f"{decision['candidate_release']})\nresult: {path}")
    return decision["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
