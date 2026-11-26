"""Coverage with hand-chosen two-dimensional vectors in place of a model."""

import json
import math

import coverage
import httpx
import pytest
import yaml
from suite.common import sha256_file

GOLDEN = [{"id": "g1", "question": "attendance rule"},
          {"id": "g2", "question": "fee refund"}]
LIVE = [
    {"id": "q1", "query": "Attendance rule?", "expected": "in_domain", "topic": "attendance"},
    {"id": "q2", "query": "how much absence is ok", "expected": "in_domain",
     "topic": "attendance"},
    {"id": "q3", "query": "hostel rooms", "expected": "out_of_coverage", "topic": "hostel"},
    {"id": "q4", "query": "hostel mess", "expected": "out_of_coverage", "topic": "hostel"},
]


@pytest.fixture(autouse=True)
def commit_id(monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc1234")


def direction(degrees):
    return [math.cos(math.radians(degrees)), math.sin(math.radians(degrees))]


# the similarity of two texts is the cosine of the angle between them
ANGLES = {"attendance policy": 5, "attendance rule": 0, "fee refund": 90, "Attendance rule?": 0,
          "how much absence is ok": 20, "hostel rooms": 225, "hostel mess": 235}


def fake_ollama(request):
    texts = json.loads(request.content)["input"]
    return httpx.Response(200, json={"embeddings": [direction(ANGLES[t]) for t in texts]})


def write_config(folder, required_share, threshold=0.9, **changes):
    values = {"threshold_similarity": threshold, "threshold_justification": "test threshold",
              "required_share": required_share, **changes}
    path = folder / "coverage_config.yaml"
    path.write_text(yaml.safe_dump({k: v for k, v in values.items() if v is not None}),
                    encoding="utf-8")
    return path


def run(tmp_path, required_share, threshold=0.9, extra=(), live=LIVE, **changes):
    for name, rows in (("golden.jsonl", GOLDEN), ("live.jsonl", live)):
        (tmp_path / name).write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    out = tmp_path / f"out{len(list(tmp_path.glob('out*')))}"
    config = write_config(tmp_path, required_share, threshold, **changes)
    argv = ["--label", "before", "--config", str(config),
            "--golden", str(tmp_path / "golden.jsonl"),
            "--live", str(tmp_path / "live.jsonl"), "--out", str(out), *extra]
    with httpx.Client(transport=httpx.MockTransport(fake_ollama)) as client:
        code = coverage.main(argv, client=client)
    files = list(out.glob("*.json"))
    return code, json.loads(files[0].read_text(encoding="utf-8")) if files else None


def test_nearest_golden_per_query():
    live = [{"id": "a", "query": "x"}, {"id": "b", "query": "y"}]
    matched = coverage.nearest_golden(
        live, GOLDEN, [direction(10), direction(70)], [direction(0), direction(90)],
        threshold=0.95, near_identical=0.999)
    assert [m["golden_id"] for m in matched] == ["g1", "g2"]
    # cos 10 degrees = 0.98481, cos 20 degrees = 0.93969
    assert matched[0]["similarity"] == pytest.approx(0.98481, abs=1e-5)
    assert matched[1]["similarity"] == pytest.approx(0.93969, abs=1e-5)
    assert [m["covered"] for m in matched] == [True, False]


def test_share_above_threshold(tmp_path):
    code, report = run(tmp_path, required_share=0.4)
    assert code == 0
    # q1 (1.0) and q2 (0.9397) are at least 0.9; the hostel queries are not
    assert report["share_covered"] == 0.5 and report["golden_count"] == 2
    # q1 is the golden question with other punctuation: without it, 1 of 3
    assert report["near_identical_count"] == 1
    assert report["share_covered_excluding_near_identical"] == pytest.approx(1 / 3)
    [topic] = report["uncovered_topics"]            # the two hostel queries group together
    assert topic["query_ids"] == ["q3", "q4"] and topic["name"] is None
    assert report["label"] == "before" and len(report["live_queries_sha256"]) == 64


def test_exit_nonzero_below_threshold(tmp_path):
    code, report = run(tmp_path, required_share=0.51)
    assert code == 1 and report["share_covered"] == 0.5      # the report is still written


def test_exit_zero_at_threshold(tmp_path):
    assert run(tmp_path, required_share=0.5)[0] == 0
    # a similarity exactly at the threshold counts as covered
    assert run(tmp_path, required_share=0.5, threshold=1.0)[1]["covered_count"] == 1


def test_topic_names_come_from_a_person_file(tmp_path):
    names = tmp_path / "names.json"
    names.write_text(json.dumps({"c1": "hostel life"}), encoding="utf-8")
    _, report = run(tmp_path, 0.5, extra=("--topic-names", str(names)))
    assert report["uncovered_topics"][0]["name"] == "hostel life"
    names.write_text(json.dumps({"c7": "x"}), encoding="utf-8")
    assert run(tmp_path, 0.5, extra=("--topic-names", str(names))) == (2, None)


def test_unreachable_model_is_exit_two_not_below(tmp_path):
    (tmp_path / "g.jsonl").write_text(json.dumps(GOLDEN[0]) + "\n", encoding="utf-8")
    argv = ["--label", "x", "--config", str(write_config(tmp_path, 0.85, 0.8)),
            "--golden", str(tmp_path / "g.jsonl"),
            "--live", str(tmp_path / "g_missing.jsonl"), "--out", str(tmp_path / "o")]
    assert coverage.main(argv) == 2


def test_values_come_from_the_config_file_and_are_recorded(tmp_path):
    _, report = run(tmp_path, 0.5)
    assert (report["threshold_similarity"], report["required_share"]) == (0.9, 0.5)
    assert report["threshold_justification"] == "test threshold"
    assert report["git_sha"] == "abc1234"
    assert report["config_sha256"] == sha256_file(tmp_path / "coverage_config.yaml")


@pytest.mark.parametrize("changes", [
    {"threshold_justification": " "}, {"threshold_justification": None},
    {"threshold_similarity": 1.2}, {"threshold_similarity": "0.8"},
    {"required_share": 0}, {"required_share": None}, {"requird_share": 0.5}])
def test_bad_config_is_exit_two(tmp_path, changes):
    values = {"required_share": 0.5, **changes}
    required = values.pop("required_share")
    assert run(tmp_path, required, **values) == (2, None)


@pytest.mark.parametrize("flag", ["--threshold", "--required-share",
                                  "--threshold-justification"])
def test_values_cannot_be_given_on_the_command_line(tmp_path, capsys, flag):
    with pytest.raises(SystemExit):
        run(tmp_path, 0.5, extra=(flag, "0.1"))
    assert f"unrecognized arguments: {flag}" in capsys.readouterr().err


def test_a_query_with_other_words_but_almost_the_same_meaning_is_near_identical(tmp_path):
    # different text, 5 degrees apart: similarity 0.9962, above the default 0.98
    live = [{"id": "q9", "query": "attendance policy", "expected": "in_domain", "topic": "t"}]
    _, report = run(tmp_path, 0.5, live=live)
    [match] = report["matches"]
    assert match["near_identical"] is True and match["covered"] is True
    assert report["share_covered_excluding_near_identical"] is None
