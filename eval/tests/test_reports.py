"""compare, drift, stochasticity and judge agreement on made-up suite results. The labels
here exist only in memory or in a temporary folder."""

import copy
import json

import compare
import drift
import judge_agreement
import pytest
import stochasticity
from suite.common import SUITE_METRICS, SuiteError

QUESTION = {
    "id": "g1", "category": "answerable", "question": "Q?", "trace_id": "t1",
    "answer": "A.", "answer_sha256": "h1", "answered_from_context": True, "error": None,
    "metrics": {"recall_at_5": 1.0}, "top5": [],
    "context": [{"source": "s.pdf", "section": "1", "page": 1, "text": "evidence"}],
    "judge": {"verdict": "unsupported", "reason": "SECRET-REASON"},
}
RUN = {
    "run_id": "r1", "release_id": "v1", "seed": 42, "golden_sha256": "g",
    "corpus_state_id": "s", "git_sha": "abc1234",
    "config": {"judge": {"model": "judge-model", "digest": "d", "prompt_sha256": "p",
                         "seed": 42},
               "module_pairs_sha256": "m"},
    "module": {"gap": 0.4}, "component": {"recall_at_5": 0.9, "list_depth": 10},
    "end_to_end": {"latency_ms_p95": 800.0}, "questions": [QUESTION],
}


def second(**top):
    run = copy.deepcopy(RUN)
    run.update(run_id="r2", **top)
    run["end_to_end"]["latency_ms_p95"] = 850.0
    run["questions"][0].update(answered_from_context=False, judge=None, trace_id="t2")
    return run


def files(tmp_path, *runs):
    paths = []
    for run in runs:
        paths.append(tmp_path / f"{run['run_id']}.json")
        paths[-1].write_text(json.dumps(run), encoding="utf-8")
    return [str(p) for p in paths]


def written(folder):
    return [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")]


def test_stochasticity_needs_a_persons_explanation(tmp_path):
    first, other = files(tmp_path, RUN, second())
    base = ["--run1", first, "--run2", other, "--out", str(tmp_path / "out")]
    assert stochasticity.main(base) == 0
    assert not (tmp_path / "out").exists()            # preview only
    assert stochasticity.main([*base, "--explanation", "too short"]) == 2
    text = "Typed in a test. " * 6
    assert stochasticity.main([*base, "--explanation", text]) == 0
    [report] = written(tmp_path / "out")
    assert report["run_ids"] == ["r1", "r2"] and report["explanation"] == text.strip()
    assert all(set(report["metrics"][m]) == {"run1", "run2", "diff"} for m in SUITE_METRICS)
    assert report["metrics"]["end_to_end.latency_ms_p95"]["diff"] == 50
    assert report["metrics"]["end_to_end.faithfulness"]["diff"] is None
    assert report["metrics"]["module.gap"]["diff"] == 0
    assert report["changed_questions"][0]["id"] == "g1"


def test_stochasticity_refuses_different_setups():
    for change in ({"seed": 7}, {"corpus_state_id": "t"}, {"release_id": "v2"}):
        with pytest.raises(SuiteError):
            stochasticity.compare_runs(RUN, second(**change))
    with pytest.raises(SuiteError):
        stochasticity.compare_runs(RUN, RUN)


def test_compare_writes_all_three_levels(tmp_path):
    first, other = files(tmp_path, RUN, second(release_id="v2"))
    argv = ["--baseline", first, "--candidate", other, "--change", "typed in a test",
            "--out", str(tmp_path / "out")]
    assert compare.main(argv) == 0
    [report] = written(tmp_path / "out")
    assert report["change"] == "typed in a test"
    for side in ("baseline", "candidate"):
        for key in ("run_id", "release_id", "golden_sha256", "corpus_state_id", "module",
                    "component", "end_to_end"):
            assert report[side][key]
    with pytest.raises(SuiteError):
        compare.build_comparison(RUN, second(corpus_state_id="t"), "x")


def test_drift_lists_changes_and_leaves_causes_blank(tmp_path):
    after = second(corpus_state_id="after-batch-b")
    report = drift.build_report(RUN, after, {})
    assert (report["before_run_id"], report["after_run_id"]) == ("r1", "r2")
    [change] = report["per_question_changes"]
    assert change["cause"] is None and report["causes_missing"] == ["g1"]
    assert change["changes"]["answered_from_context"] == {"before": True, "after": False}
    assert report["metric_deltas"]["end_to_end.latency_ms_p95"]["delta"] == 50
    stated = drift.build_report(RUN, after, {"g1": {"type": "data", "cause": "test"}})
    assert stated["per_question_changes"][0]["cause"] == "test"
    for bad in ({"g9": {"type": "data", "cause": "x"}}, {"g1": {"type": "other"}}):
        with pytest.raises(SuiteError):
            drift.build_report(RUN, after, bad)
    with pytest.raises(SuiteError):
        drift.build_report(RUN, second(golden_sha256="v2"), {})


def test_drift_needs_the_same_seed_and_judge():
    report = drift.build_report(RUN, second(corpus_state_id="after"), {})
    assert (report["same_release"], report["same_corpus_state"]) == (True, False)
    report = drift.build_report(RUN, second(release_id="v2"), {})
    assert (report["same_release"], report["same_corpus_state"]) == (False, True)
    with pytest.raises(SuiteError):
        drift.build_report(RUN, second(seed=7), {})
    for key in ("model", "digest", "prompt_sha256", "seed"):
        for value in ("other", None):
            after = second()
            after["config"]["judge"][key] = value
            with pytest.raises(SuiteError):
                drift.build_report(RUN, after, {})


def test_blind_view_shows_no_verdict():
    text = judge_agreement.blind_view(RUN)
    assert "evidence" in text and "ANSWER: A." in text and "t1" in text
    for hidden in ("unsupported", "SECRET-REASON", "judge-model", "verdict"):
        assert hidden not in text


def label(**changes):
    return {"trace_id": "t1", "answer_sha256": "h1", "human_label": "unsupported",
            "labeller": "test", "labelled_at": "2026-01-01", **changes}


def test_judge_agreement_counts_matches():
    report = judge_agreement.score([RUN], [label()])
    assert (report["labelled"], report["agreements"], report["agreement_rate"]) == (1, 1, 1)
    assert report["judge_model"] == "judge-model"
    assert judge_agreement.score([RUN], [label(human_label="supported")])["agreements"] == 0
    for bad in ([label(answer_sha256="other")], [label(trace_id="t9")],
                [label(), label()], [label(human_label="yes")]):
        with pytest.raises(SuiteError):
            judge_agreement.score([RUN], bad)
