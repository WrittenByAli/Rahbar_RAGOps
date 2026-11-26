"""The whole suite against a fake API and a fake Ollama. All numbers here are made up for
the test: nothing is a measurement."""

import json
import re
from pathlib import Path

import httpx
import pytest
import run_suite
from suite import component, end_to_end, judge, module
from suite.common import SUITE_METRICS, get, load_jsonl, sha256_file

GOLDEN = [
    {"id": "g1", "question": "What attendance percentage is required?",
     "category": "answerable", "requires_batch": "A", "added_in": "v1",
     "relevant": [{"doc_id": "rules", "page": 1}]},
    {"id": "g2", "question": "Which grading policy applies to repeated courses?",
     "category": "answerable", "requires_batch": "B", "added_in": "v1",
     "relevant": [{"doc_id": "grades", "page": 2}]},
    {"id": "g3", "question": "Where are the hostel rooms?", "category": "unanswerable"},
]
PAIRS = [{"id": "p1", "type": "paraphrase", "a": "same", "b": "same"},
         {"id": "u1", "type": "unrelated", "a": "same", "b": "other"}]
CHUNK = {"chunk_id": "c1", "doc_id": "rules", "doc_version": "v1", "page": 1,
         "page_start": 1, "page_end": 1, "score": 0.9, "rank": 1, "kept": True}
ABSENT = object()        # the API does not send the key at all
CONTEXT = [{"chunk_id": "c1", "source": "rules.pdf", "section": "1", "page": 1,
            "text": "Students need 80 percent attendance."}]


class Fake:
    """Answers like the API and like Ollama. g1 is answered, g2 and g3 are declined."""

    def __init__(self, judge_reply='{"verdict": "supported", "reason": "stated"}',
                 answers=("g1",), failing=(), debug=True, generations=None,
                 lake_states=("lake-1",), warmup="answers", cache_status="bypass",
                 trace_generation="gen1", ask_release="v1"):
        self.judge_reply, self.answers, self.failing = judge_reply, answers, failing
        self.debug, self.generations = debug, generations
        self.lake_states, self.warmup = lake_states, warmup
        self.cache_status, self.trace_generation = cache_status, trace_generation
        self.ask_release = ask_release
        self.asked, self.retrieved, self.calls = [], 0, []

    def __call__(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.calls.append(path)
        if path == "/api/retrieve":
            self.retrieved += 1
            return httpx.Response(200, json={
                "release_id": "v1", "collection": self._collection(),
                "embedding": {"model": "fake-embed", "version": "d1"}, "top_k": body["k"],
                "filters_applied": [{"name": "relevance_floor", "value": 0.55}],
                "retrieved": [CHUNK]})
        if path == "/api/ask":
            return self._ask(body)
        if path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [
                [0.0, 1.0] if text == "other" else [1.0, 0.0] for text in body["input"]]})
        if path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": self.judge_reply}})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404)

    def _collection(self):
        late = self.generations and self.retrieved > 1
        collection = {"name": "rahbar", "version": "gen2" if late else "gen1"}
        # the first value on the first request, the last one afterwards
        state = self.lake_states[0 if self.retrieved <= 1 else -1]
        if state is not ABSENT:
            collection["lake_state_id"] = state
        return collection

    def _ask(self, body):
        assert body["bypass_cache"] is True and body["include_trace"] is True
        self.asked.append(body["question"])
        qid = next((q["id"] for q in GOLDEN if q["question"] == body["question"]), "warmup")
        failed = qid in self.failing
        answered = qid in self.answers
        trace = {
            "trace_id": f"t-{qid}", "release_id": self.ask_release, "git_sha": "abc1234",
            "collection": {"name": "rahbar", "version": self.trace_generation},
            "embedding": {"model": "fake-embed", "version": "d1"},
            "llm": {"model": "fake-llm", "version": "d2", "params": {"seed": 42}},
            "prompt": {"template_version": "p1"}, "retrieved": [CHUNK], "context": CONTEXT,
            "cache": {"status": self.cache_status},
            "response": {"text": "80 percent." if answered else "I don't know.",
                         "answered_from_context": None if failed else answered},
            "citations_raw": [{"source": "rules.pdf", "section": "1", "page": 1},
                              {"source": "made-up.pdf", "section": "9"}]
            if answered else [],
            "latency_ms": {"total": {"g1": 100.0, "g2": 200.0, "g3": 300.0}.get(qid, 9e9)},
        }
        if qid == "warmup" and self.warmup == "abstains":
            trace.update(prompt=None, context=[])      # the model was not called
        if failed or (qid == "warmup" and self.warmup == "fails"):
            trace["error"] = {"stage": "generate", "type": "ReadTimeout"}
        return httpx.Response(200, json={"answer": trace["response"]["text"],
                                         "trace": trace if self.debug else None})


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc1234")
    for name in ("EVAL_SEED", "EVAL_GOLDEN"):
        monkeypatch.delenv(name, raising=False)
    for name, rows in (("golden.jsonl", GOLDEN), ("pairs.jsonl", PAIRS)):
        (tmp_path / name).write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp_path


def run(folder, fake, *extra):
    argv = ["--target", "http://api", "--label", "test", "--out", str(folder / "out"),
            "--golden", str(folder / "golden.jsonl"), "--pairs", str(folder / "pairs.jsonl"),
            "--ollama-url", "http://ollama", "--judge-model", "fake-judge", *extra]
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        code = run_suite.main(argv, client=client)
    written = sorted((folder / "out").glob("*.json"))
    return code, json.loads(written[-1].read_text(encoding="utf-8")) if written else None


def test_one_command_writes_timestamped_result_and_summary(files, capsys):
    code, result = run(files, Fake())

    assert code == 0
    [path] = (files / "out").glob("*.json")
    assert re.fullmatch(r"\d{8}T\d{6}Z-test\.json", path.name)
    assert path.stem == result["run_id"]
    assert result["corpus_state_id"] == "lake-1"
    assert result["git_sha"] == "abc1234" and result["release_id"] == "v1"
    assert result["golden_sha256"] == sha256_file(files / "golden.jsonl")
    assert (result["seed"], result["golden_version"]) == (42, "v1")
    for name in SUITE_METRICS:
        assert get(result, name) is not None, name
    assert result["summary_table"] in capsys.readouterr().out
    assert "end_to_end.faithfulness" in result["summary_table"]

    # module: identical vectors give 1, orthogonal give 0; the fake is deterministic
    module = result["module"]
    assert (module["paraphrase_mean"], module["unrelated_mean"], module["gap"]) == (1, 0, 1)
    assert module["determinism_max_abs_diff"] == 0
    assert module["determinism_after_reload_max_abs_diff"] == 0
    assert module["embed_ms_per_chunk"] > 0 and module["embed_chunks_timed"] == 1

    # component: the one chunk matches g1 and not g2
    comp = result["component"]
    assert (comp["recall_at_5"], comp["mrr"], comp["n_questions"]) == (0.5, 0.5, 2)
    assert comp["corpus_a_subset"] == {"recall_at_3": 1, "recall_at_5": 1, "mrr": 1,
                                       "ndcg_at_5": 1, "n_questions": 1}
    assert comp["noise"]["delta"]["recall_at_5"] == 0

    e2e = result["end_to_end"]
    assert (e2e["faithfulness"], e2e["faithfulness_n"]) == (1, 1)
    assert (e2e["abstention_correctness"], e2e["abstention_n"]) == (1, 1)
    assert (e2e["citation_validity"], e2e["citation_n"]) == (0.5, 2)     # raw citations
    # the warm-up (9e9 ms) is not in the latencies 100, 200, 300
    assert (e2e["latency_ms_p50"], e2e["latency_ms_p95"], e2e["latency_n"]) == (200, 300, 3)
    assert e2e["answerable_answer_rate"] == 0.5
    # only g1 was answered: abstentions are quick and would flatter the figure
    assert (e2e["latency_ms_p50_answered"], e2e["latency_ms_p95_answered"],
            e2e["latency_answered_n"]) == (100, 100, 1)
    by_id = {q["id"]: q for q in result["questions"]}
    assert by_id["g1"]["trace_id"] == "t-g1"
    assert by_id["g1"]["judge"]["verdict"] == "supported"
    assert by_id["g2"]["judge"] is None


def test_api_without_the_lake_state_key_gives_a_one_collection_id(files):
    code, result = run(files, Fake(lake_states=(ABSENT,)))
    assert code == 0 and result["corpus_state_id"] == "index:rahbar@gen1"


@pytest.mark.parametrize("value", [None, ""])
def test_null_lake_state_is_refused(files, capsys, value):
    # the collection is being written, or does not record what it was built from
    fake = Fake(lake_states=(value,))
    assert run(files, fake) == (2, None)
    assert "lake_state_id" in capsys.readouterr().err and not fake.asked


@pytest.mark.parametrize("states", [
    ("lake-1", "lake-2"), ("lake-1", None), ("lake-1", ABSENT), (ABSENT, "lake-1")])
def test_lake_state_change_during_the_run_is_refused(files, capsys, states):
    assert run(files, Fake(lake_states=states)) == (2, None)
    assert "changed during the run" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--corpus-state-id", "--ingest-reports"])
def test_corpus_state_cannot_be_supplied_by_hand(files, flag):
    with pytest.raises(SystemExit):
        run(files, Fake(), flag, "x")


@pytest.mark.parametrize("warmup", ["abstains", "fails"])
def test_warm_up_that_never_reaches_the_model_is_refused(files, capsys, warmup):
    fake = Fake(warmup=warmup)
    assert run(files, fake) == (2, None)
    assert "warm-up" in capsys.readouterr().err
    assert len(fake.asked) == 1


def test_warm_up_question_is_in_no_question_set():
    def words(text):
        return re.findall(r"\w+", text.lower())

    eval_dir = Path(run_suite.__file__).parent
    paths = [*eval_dir.glob("golden*.jsonl"), eval_dir / "live_queries.jsonl"]
    questions = [row.get("question") or row.get("query") for path in paths
                 for row in load_jsonl(path)]
    assert questions
    assert words(end_to_end.WARMUP_QUESTION) not in [words(q) for q in questions]
    assert end_to_end.WARMUP_QUESTION != "What does this knowledge base cover?"


def test_unparseable_verdict_is_an_error_never_supported(files):
    for reply in ("supported", "The answer is not supported.", '{"verdict": "Supported!"}',
                  "[1]", 'Sure: {"verdict": "supported"}'):
        assert judge.parse_verdict(reply)["verdict"] == "error", reply
    fenced = '```json\n{"verdict": "unsupported", "reason": "r"}\n```'
    assert judge.parse_verdict(fenced)["verdict"] == "unsupported"
    code, result = run(files, Fake(judge_reply="yes, supported"))
    # the only verdict is an error: there is no measurement
    assert code == 0 and result["end_to_end"]["faithfulness"] is None
    assert result["end_to_end"]["faithfulness_judge_errors"] == 1


def test_one_judge_error_among_two_counts_against_faithfulness():
    def record(verdict):
        return {"category": "answerable", "answered_from_context": True, "error": None,
                "judge": {"verdict": verdict}, "latency_ms": 1, "citations_raw": [],
                "citations_raw_valid": 0}
    block = end_to_end.summarise([record("supported"), record("error")])
    assert (block["faithfulness"], block["faithfulness_judge_errors"]) == (0.5, 1)
    assert block["citation_validity"] is None and block["abstention_correctness"] is None


def test_errors_are_not_abstentions(files):
    code, result = run(files, Fake(failing=("g3",)))
    e2e = result["end_to_end"]
    assert code == 0 and e2e["errors"] == 1
    assert e2e["abstention_correctness"] is None      # the only unanswerable one failed
    assert e2e["latency_n"] == 2
    by_id = {q["id"]: q for q in result["questions"]}
    assert by_id["g3"]["answered_from_context"] is None


def test_pipeline_that_never_answers_gets_null_not_one(files):
    code, result = run(files, Fake(answers=()))
    e2e = result["end_to_end"]
    assert code == 0
    assert e2e["faithfulness"] is None and e2e["citation_validity"] is None
    assert e2e["abstention_correctness"] == 1 and e2e["answerable_answer_rate"] == 0


def test_server_without_debug_traces_is_refused(files, capsys):
    fake = Fake(debug=False)
    code, result = run(files, fake)
    assert code == 2 and result is None
    assert "EVAL_DEBUG_ENABLED" in capsys.readouterr().err
    assert len(fake.asked) == 1        # it stopped at the warm-up request


def test_index_change_during_the_run_is_refused(files, capsys):
    code, result = run(files, Fake(generations=True))
    assert code == 2 and result is None
    assert "index_generation changed" in capsys.readouterr().err


def test_failed_retrieval_gives_null_component_metrics():
    def broken(request):
        return httpx.Response(500)
    with httpx.Client(transport=httpx.MockTransport(broken)) as client:
        block, _, _ = component.run_component(client, "http://api", GOLDEN, 42, 10)
    assert block["recall_at_5"] is None and len(block["errors"]) == 2


def test_typos_are_seeded():
    question = "What is the minimum attendance requirement for final examinations?"
    noisy = component.add_typos(question, 42, "g1")
    assert noisy == component.add_typos(question, 42, "g1")
    assert noisy != question
    others = {component.add_typos(question, seed, "g1") for seed in range(5)}
    assert len(others) > 1
    assert component.add_typos("Is it on 1.5?", 42, "g1") == "Is it on 1.5?"


def test_a_cached_answer_invalidates_the_run(files, capsys):
    assert run(files, Fake(cache_status="hit")) == (2, None)
    assert "cache must be bypassed" in capsys.readouterr().err


def test_a_trace_from_another_index_generation_invalidates_the_run(files, capsys):
    assert run(files, Fake(trace_generation="gen9")) == (2, None)
    assert "index_generation changed during the run" in capsys.readouterr().err


def test_a_trace_from_another_release_invalidates_the_run(files, capsys):
    assert run(files, Fake(ask_release="v2")) == (2, None)
    assert "release_id changed during the run" in capsys.readouterr().err


def test_an_answer_to_an_unanswerable_question_is_not_judged(files):
    code, result = run(files, Fake(answers=("g1", "g3")))
    by_id = {q["id"]: q for q in result["questions"]}
    e2e = result["end_to_end"]
    assert code == 0 and by_id["g3"]["judge"] is None
    assert e2e["faithfulness_n"] == 1                 # g1 only
    assert e2e["abstention_correctness"] == 0         # but g3 was not declined


def test_component_metrics_use_the_list_before_the_floor():
    below_floor = {"doc_id": "grades", "doc_version": None, "page_start": 2, "page_end": 2,
                   "kept": False, "dropped_by": "relevance_floor"}

    def handler(request):
        return httpx.Response(200, json={"retrieved": [below_floor]})

    g2 = [q for q in GOLDEN if q["id"] == "g2"]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        block, _, _ = component.run_component(client, "http://api", g2, 42, 10)
    assert block["recall_at_5"] == 1 and block["mrr"] == 1
    assert block["after_floor"]["recall_at_5"] == 0 and block["after_floor"]["mrr"] == 0


def test_judge_request_is_deterministic_and_carries_the_context():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"verdict": "supported"}'},
                                         "prompt_eval_count": 100})

    context = [{"source": "s.pdf", "section": "1", "text": "Students need 80 percent."}]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        verdict = judge.judge_answer(client, "http://o", "m", 7, "Q?", "The answer.", context)
    assert verdict["verdict"] == "supported"
    body = sent[0]
    assert body["options"]["temperature"] == 0 and body["options"]["seed"] == 7
    user = body["messages"][1]["content"]
    assert "Students need 80 percent." in user and "The answer." in user and "Q?" in user


def test_judge_prompt_that_filled_the_window_is_an_error():
    def handler(request):
        return httpx.Response(200, json={"message": {"content": '{"verdict": "supported"}'},
                                         "prompt_eval_count": judge.NUM_CTX})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        verdict = judge.judge_answer(client, "http://o", "m", 7, "Q?", "A.", [])
    assert verdict["verdict"] == "error"


def test_embeddings_are_compared_across_a_real_model_unload():
    unloaded = []

    def handler(request):
        body = json.loads(request.content)
        if body.get("keep_alive") == 0:
            unloaded.append(True)
        # after the unload the "model" answers slightly differently
        shift = 0.25 if unloaded else 0.0
        return httpx.Response(200, json={"embeddings": [[1.0 + shift, 0.0]]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        block = module.timing_and_determinism(client, "http://o", "m", ["chunk text"])
    assert unloaded
    assert block["determinism_max_abs_diff"] == 0
    assert block["determinism_after_reload_max_abs_diff"] == 0.25
