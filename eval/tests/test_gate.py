"""The gate on made-up suite results and a temporary thresholds file."""

import copy
import json

import pytest
import yaml

import gate

LIMITS = {"recall_at_5": 0.02, "faithfulness": 0.8, "latency_ms_p95": 1000,
          "abstention_correctness": 0, "answerable_answer_rate": 0.7}
GATE_YAML = yaml.safe_dump({"rules": {
    name: {"limit": limit, "justification": "test value"}
    for name, limit in LIMITS.items()}})
BASELINE = {
    "run_id": "base", "release_id": "v1", "golden_sha256": "g", "corpus_state_id": "s",
    "seed": 42, "git_sha": "aaaaaaa",
    "config": {"judge": {"model": "j", "digest": "d", "prompt_sha256": "p", "seed": 42},
               "module_pairs_sha256": "m", "llm": {"model": "generator-1"}},
    "component": {"list_depth": 10, "errors": [],
                  "recall_at_3": 0.8, "recall_at_5": 0.9, "mrr": 0.7, "ndcg_at_5": 0.75,
                  "n_questions": 20},
    "end_to_end": {"faithfulness": 0.9, "abstention_correctness": 0.875,
                   "citation_validity": 0.95, "latency_ms_p50": 500,
                   "latency_ms_p95": 800, "answerable_answer_rate": 0.9,
                   "abstention_n": 8, "answerable_n": 20, "errors": 0},
    "questions": [{"id": "g1"}, {"id": "g2"}],
}


def candidate(**changes):
    result = copy.deepcopy(BASELINE)
    result.update(run_id="cand", release_id="v2")
    for key, value in changes.items():
        level, name = key.split("__")
        result[level][name] = value
    return result


@pytest.fixture
def run_gate(tmp_path):
    config = tmp_path / "gate-thresholds.yaml"
    config.write_text(yaml.safe_dump({
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "rahbar-gate-thresholds"},
        "data": {"gate.yaml": GATE_YAML}}), encoding="utf-8")

    def run(after, before=BASELINE, thresholds=config):
        for name, data in (("before.json", before), ("after.json", after)):
            (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
        out = tmp_path / f"out{len(list(tmp_path.glob('out*')))}"
        code = gate.main(["--baseline", str(tmp_path / "before.json"),
                          "--candidate", str(tmp_path / "after.json"),
                          "--thresholds", str(thresholds), "--out", str(out)])
        files = list(out.glob("*.json"))
        return code, json.loads(files[0].read_text(encoding="utf-8")) if files else None

    run.folder = tmp_path
    return run


def failed(decision):
    return {r["name"] for r in decision["rules"] if not r["passed"]}


def test_gate_passes_good_candidate(run_gate):
    # exactly on every limit: 0.9 - 0.88 is a little over 0.02 in floating point
    code, decision = run_gate(candidate(
        component__recall_at_5=0.88, end_to_end__faithfulness=0.8,
        end_to_end__latency_ms_p95=1000, end_to_end__answerable_answer_rate=0.7))
    assert code == 0 and decision["decision"] == "pass" and decision["exit_code"] == 0
    assert failed(decision) == set()
    assert decision["candidate_release"] == "v2"
    assert (decision["baseline_run_id"], decision["candidate_run_id"]) == ("base", "cand")
    assert len(decision["thresholds_sha256"]) == 64


@pytest.mark.parametrize("rule, change", [
    ("recall_at_5", {"component__recall_at_5": 0.87}),
    ("faithfulness", {"end_to_end__faithfulness": 0.79}),
    ("latency_ms_p95", {"end_to_end__latency_ms_p95": 1001}),
    ("abstention_correctness", {"end_to_end__abstention_correctness": 0.75}),
    ("answerable_answer_rate", {"end_to_end__answerable_answer_rate": 0.65}),
])
def test_gate_fails_each_rule(run_gate, rule, change):
    code, decision = run_gate(candidate(**change))
    assert code == 1 and decision["decision"] == "fail" and decision["exit_code"] == 1
    assert failed(decision) == {rule}


def test_missing_metric_fails_not_passes(run_gate):
    for key in gate.GUARDED_METRICS:
        level, name = key.split(".")
        code, decision = run_gate(candidate(**{f"{level}__{name}": None}))
        assert code == 1 and "no_null_metric" in failed(decision), key
    gone = candidate()
    del gone["end_to_end"]["faithfulness"]
    code, decision = run_gate(gone)
    assert code == 1 and failed(decision) == {"no_null_metric", "faithfulness"}


def test_always_abstain_pipeline_fails(run_gate):
    code, decision = run_gate(candidate(
        end_to_end__abstention_correctness=1.0, end_to_end__faithfulness=None,
        end_to_end__citation_validity=None, end_to_end__latency_ms_p95=5,
        end_to_end__answerable_answer_rate=0.0))
    assert code == 1
    assert {"no_null_metric", "faithfulness", "answerable_answer_rate"} <= failed(decision)


def test_mismatched_golden_or_corpus_is_error(run_gate):
    # golden_sha256 must match (same questions required); corpus_state_id may differ
    # across data updates — that is the data-drift scenario the gate is designed for.
    code, decision = run_gate({**candidate(), "golden_sha256": "different"})
    assert (code, decision) == (2, None), "golden_sha256 mismatch"
    assert run_gate({**candidate(), "golden_sha256": None})[0] == 2
    # corpus_state_id mismatch is allowed and must NOT raise an error
    code, decision = run_gate({**candidate(), "corpus_state_id": "different"})
    assert code != 2, "corpus_state_id mismatch should not be an error"


def test_other_invalid_comparisons_exit_two(run_gate):
    assert run_gate(candidate(component__n_questions=19))[0] == 2
    assert run_gate(candidate(end_to_end__abstention_n=7))[0] == 2
    assert run_gate(candidate(end_to_end__answerable_n=19))[0] == 2
    assert run_gate({**candidate(), "questions": [{"id": "g1"}]})[0] == 2
    assert run_gate(BASELINE)[0] == 2                    # the same run twice
    unusable = copy.deepcopy(BASELINE)
    unusable["end_to_end"]["faithfulness"] = None
    assert run_gate(candidate(), before=unusable)[0] == 2
    # a crash on a malformed file must not look like a "fail"
    assert run_gate({**candidate(), "questions": 7})[0] == 2


def test_bad_thresholds_exit_two(run_gate):
    path = run_gate.folder / "bad.yaml"
    rules = yaml.safe_load(GATE_YAML)["rules"]
    for broken in (
        {k: v for k, v in rules.items() if k != "faithfulness"},
        {**rules, "recal_at_5": rules["recall_at_5"]},
        {**rules, "faithfulness": {"limit": 0.8}},
        {**rules, "faithfulness": {"limit": "0.8", "justification": "x"}},
    ):
        path.write_text(yaml.safe_dump({"rules": broken}), encoding="utf-8")
        assert run_gate(candidate(), thresholds=path)[0] == 2
    assert run_gate(candidate(), thresholds=run_gate.folder / "absent.yaml")[0] == 2


def test_mounted_file_and_configmap_give_the_same_hash(run_gate):
    mounted = run_gate.folder / "gate.yaml"
    mounted.write_text(GATE_YAML, encoding="utf-8")
    _, from_configmap = run_gate(candidate())
    _, from_mount = run_gate(candidate(), thresholds=mounted)
    assert from_mount["thresholds_sha256"] == from_configmap["thresholds_sha256"]


def test_changing_only_the_thresholds_flips_the_decision(run_gate):
    strict = run_gate.folder / "strict.yaml"
    strict.write_text(GATE_YAML.replace("limit: 1000", "limit: 700"), encoding="utf-8")
    assert run_gate(candidate())[0] == 0
    assert run_gate(candidate(), thresholds=strict)[0] == 1


def test_request_errors_fail_the_candidate(run_gate):
    for change in ({"end_to_end__errors": 5},
                   {"component__errors": [{"id": "g1", "error": "ReadTimeout"}]},
                   {"end_to_end__errors": None}, {"component__errors": None}):
        code, decision = run_gate(candidate(**change))
        assert code == 1 and failed(decision) == {"no_request_errors"}, change
    gone = candidate()
    del gone["end_to_end"]["errors"]
    code, decision = run_gate(gone)
    assert code == 1 and failed(decision) == {"no_request_errors"}


@pytest.mark.parametrize("level, value", [
    ("end_to_end", 1), ("component", [{"id": "g1"}]),
    ("end_to_end", None), ("component", None)])
def test_baseline_with_request_errors_is_invalid(run_gate, level, value):
    before = copy.deepcopy(BASELINE)
    before[level]["errors"] = value
    assert run_gate(candidate(), before=before) == (2, None)


def holder_of(result, parents):
    for parent in parents:
        result = result[parent]
    return result


@pytest.mark.parametrize("path", [
    "seed", "config.judge.model", "config.judge.digest", "config.judge.prompt_sha256",
    "config.judge.seed", "config.module_pairs_sha256", "component.list_depth"])
def test_different_measurement_settings_are_invalid(run_gate, path):
    *parents, key = path.split(".")
    for value in ("other", None):
        after = candidate()
        holder_of(after, parents)[key] = value
        assert run_gate(after) == (2, None), (path, value)
    # missing on both sides is not "the same"
    after, before = candidate(), copy.deepcopy(BASELINE)
    for result in (after, before):
        del holder_of(result, parents)[key]
    assert run_gate(after, before=before) == (2, None)


def test_generator_and_code_may_differ(run_gate):
    after = candidate()
    after["config"]["llm"]["model"] = "generator-2"
    after["git_sha"] = "bbbbbbb"
    assert run_gate(after)[0] == 0


@pytest.mark.parametrize("rule, limit", [
    ("recall_at_5", 1.5), ("recall_at_5", -0.1), ("faithfulness", 80),
    ("faithfulness", -1), ("abstention_correctness", 2),
    ("answerable_answer_rate", 1.01), ("latency_ms_p95", 0), ("latency_ms_p95", -5)])
def test_out_of_range_limit_is_refused(run_gate, rule, limit):
    rules = yaml.safe_load(GATE_YAML)["rules"]
    rules[rule]["limit"] = limit
    path = run_gate.folder / "range.yaml"
    path.write_text(yaml.safe_dump({"rules": rules}), encoding="utf-8")
    assert run_gate(candidate(), thresholds=path) == (2, None)
