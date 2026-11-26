"""P.3: cached answers belong to one release and one index state, and can be bypassed.

Findings F-03 and F-04: the A2 key was only a hash of the question, so a candidate could be
answered from the live release's cache, and a re-index did not invalidate anything.

Redis, ChromaDB and httpx are in-memory fakes: no cluster, model or network.
"""

from __future__ import annotations

import fnmatch
import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.schemas import Answer, AskResponse, Citation, Passage
from app.services.answer import answer_question

QUESTION = "What is the attendance requirement?"
PARAPHRASE = "How much attendance do I need?"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class RecordingRedis:
    """In-memory Redis that also records every operation that reaches it."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []

    def get(self, key: str) -> str | None:
        self.calls.append(("get", key))
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.calls.append(("set", key))
        self.store[key] = value

    def delete(self, key: str) -> None:
        self.calls.append(("delete", key))
        self.store.pop(key, None)

    def scan_iter(self, pattern: str) -> list[str]:
        self.calls.append(("scan", pattern))
        return [k for k in list(self.store) if fnmatch.fnmatchcase(k, pattern)]


class FixedRetriever:
    name = "chroma"

    def __init__(self, generation: str = "0") -> None:
        self.generation = generation

    def retrieve(self, question: str, k: int) -> list[Passage]:
        return [Passage(chunk_id="c1", text="80% attendance is required.",
                        source="handbook.pdf", section="1.21", page=4, score=0.9)]

    def index_generation(self) -> str:
        return self.generation


class SayingProvider:
    """Always gives the same answer text and counts how often the model was 'called'."""

    name = "saying"

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        self.calls += 1
        return Answer(
            reasoning="test",
            answer=self.text,
            citations=[Citation(source="handbook.pdf", section="1.21", page=4)],
            answered_from_context=True,
            confidence="high",
        )


class FakeChromaCollection:
    def __init__(self) -> None:
        self.metadata: dict[str, Any] | None = {"embed_model": "nomic-embed-text"}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "ids": [["c1"]],
            "distances": [[0.1]],
            "metadatas": [[{"source": "handbook.pdf", "section": "1.21", "page": 4}]],
            "documents": [["80% attendance is required."]],
        }


class FakeEmbedResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"embeddings": [[0.1, 0.2, 0.3]]}


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> RecordingRedis:
    import redis

    fake = RecordingRedis()
    monkeypatch.setattr(redis.Redis, "from_url", lambda *a, **k: fake)
    return fake


@pytest.fixture()
def chroma(monkeypatch: pytest.MonkeyPatch) -> FakeChromaCollection:
    """Lets the real ChromaRetriever run against an in-memory collection."""
    import chromadb
    import httpx

    collection = FakeChromaCollection()

    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def get_collection(self, name: str) -> FakeChromaCollection:
            return collection

    monkeypatch.setattr(chromadb, "HttpClient", FakeClient)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeEmbedResponse())
    return collection


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "retriever": "chroma", "release_id": "v1", "collection": "rahbar-v1",
        "prompt_version": "p1",
    }
    values.update(overrides)
    return Settings(**values)


# --------------------------------------------------------------------------- #
# The three required behaviours
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("candidate_change", [
    {"release_id": "v2"},
    {"collection": "rahbar-v2"},
    {"prompt_version": "p2"},
])
def test_candidate_never_reads_live_entry(
    fake_redis: RecordingRedis, candidate_change: dict[str, str]
) -> None:
    live, candidate = _settings(), _settings(**candidate_change)
    live_model = SayingProvider("live answer")
    candidate_model = SayingProvider("candidate answer")

    answer_question(QUESTION, FixedRetriever(), live_model, live)
    # Control: the live release does hit its own entry, so the cache really works here.
    assert answer_question(QUESTION, FixedRetriever(), live_model, live).answer == "live answer"
    assert live_model.calls == 1

    resp = answer_question(QUESTION, FixedRetriever(), candidate_model, candidate)

    assert resp.answer == "candidate answer"
    assert candidate_model.calls == 1
    # The candidate did not overwrite the live entry either.
    assert answer_question(QUESTION, FixedRetriever(), live_model, live).answer == "live answer"
    assert live_model.calls == 1


def test_reindex_invalidates_entries(
    fake_redis: RecordingRedis, chroma: FakeChromaCollection
) -> None:
    from app.retrieval.chroma import ChromaRetriever

    settings = _settings()
    retriever = ChromaRetriever(settings)
    old = SayingProvider("answer from the old index")
    new = SayingProvider("answer from the new index")

    answer_question(QUESTION, retriever, old, settings)
    # Same index generation: still a hit.
    assert answer_question(QUESTION, retriever, new, settings).answer == old.text
    assert new.calls == 0

    # What the ingestion Job does after it changes the index.
    assert chroma.metadata is not None
    chroma.metadata["index_generation"] = 1

    assert answer_question(QUESTION, retriever, new, settings).answer == new.text
    assert new.calls == 1


def test_bypass_skips_read_and_write(fake_redis: RecordingRedis) -> None:
    settings = _settings()
    first = SayingProvider("cached answer")
    fresh = SayingProvider("fresh answer")
    answer_question(QUESTION, FixedRetriever(), first, settings)
    stored = dict(fake_redis.store)
    assert stored                                   # there is an entry that could be read
    fake_redis.calls.clear()

    resp = answer_question(QUESTION, FixedRetriever(), fresh, settings, bypass_cache=True)

    assert resp.answer == "fresh answer"
    assert fresh.calls == 1
    assert fake_redis.calls == []                   # no read and no write reached Redis
    assert fake_redis.store == stored

    # A bypassed question that was never cached leaves nothing behind.
    answer_question("A new question?", FixedRetriever(), fresh, settings, bypass_cache=True)
    assert fake_redis.store == stored

    # Bypass lasts for one request only.
    assert answer_question(QUESTION, FixedRetriever(), fresh, settings).answer == "cached answer"


# --------------------------------------------------------------------------- #
# Key construction
# --------------------------------------------------------------------------- #
def test_key_names_release_collection_generation_prompt_and_question() -> None:
    from app.services.cache import AnswerCache

    key = AnswerCache(_settings(), index_generation="7")._hash_key("  What   IS the rule? ")

    digest = hashlib.sha256(b"what is the rule?").hexdigest()
    assert key == f"cache:v1:rahbar-v1:7:p1:{digest}"


def test_separator_inside_a_part_cannot_make_two_namespaces_collide() -> None:
    from app.services.cache import AnswerCache

    one = AnswerCache(_settings(release_id="a:b", collection="c"))
    two = AnswerCache(_settings(release_id="a", collection="b:c"))

    assert one._hash_key(QUESTION) != two._hash_key(QUESTION)


def test_similarity_lookup_stays_inside_its_namespace(fake_redis: RecordingRedis) -> None:
    """The A2 similarity branch is unused by the app (D-09) but must not cross releases."""
    from app.services.cache import AnswerCache

    stored = AskResponse(request_id="r", answer="live answer", citations=[],
                         answered_from_context=True, confidence="high", retrieved=[],
                         provider="saying")
    live = AnswerCache(_settings())
    live.put(QUESTION, stored, question_vec=[1.0, 0.0])

    assert live.get(PARAPHRASE, question_vec=[1.0, 0.0]) is not None
    candidate = AnswerCache(_settings(release_id="v2"))
    assert candidate.get(PARAPHRASE, question_vec=[1.0, 0.0]) is None
    reindexed = AnswerCache(_settings(), index_generation="1")
    assert reindexed.get(PARAPHRASE, question_vec=[1.0, 0.0]) is None


def test_the_application_matches_exactly_and_never_by_similarity(
    fake_redis: RecordingRedis,
) -> None:
    model = SayingProvider("an answer")
    answer_question(QUESTION, FixedRetriever(), model, _settings())
    answer_question(PARAPHRASE, FixedRetriever(), model, _settings())

    assert model.calls == 2
    assert not [c for c in fake_redis.calls if c[0] == "scan"]
    assert not [k for k in fake_redis.store if k.startswith("vec:")]


# --------------------------------------------------------------------------- #
# Index generation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("metadata", "expected"), [
    ({"index_generation": 7}, "7"),
    ({"index_generation": "12"}, "12"),
    ({"embed_model": "nomic-embed-text"}, "0"),
    (None, "0"),
])
def test_index_generation_comes_from_collection_metadata(
    chroma: FakeChromaCollection, metadata: dict[str, Any] | None, expected: str
) -> None:
    from app.retrieval.chroma import ChromaRetriever

    chroma.metadata = metadata

    assert ChromaRetriever(_settings()).index_generation() == expected


def test_stub_retriever_index_generation_is_zero() -> None:
    from app.retrieval.stub import StubRetriever

    assert StubRetriever().index_generation() == "0"


def test_unreadable_generation_skips_the_cache_but_still_answers(
    fake_redis: RecordingRedis,
) -> None:
    """Guessing a generation could serve an answer from before a re-index."""

    class NoGeneration(FixedRetriever):
        def index_generation(self) -> str:
            raise ConnectionError("chroma metadata unavailable")

    resp = answer_question(QUESTION, NoGeneration(), SayingProvider("an answer"), _settings())

    assert resp.answer == "an answer"
    assert fake_redis.calls == []


# --------------------------------------------------------------------------- #
# Settings, failures, trace ids, route
# --------------------------------------------------------------------------- #
def test_cache_enabled_false_never_touches_redis(fake_redis: RecordingRedis) -> None:
    from app.trace import new_trace

    settings = _settings(cache_enabled=False)
    model = SayingProvider("an answer")
    answer_question(QUESTION, FixedRetriever(), model, settings)
    record = new_trace(QUESTION, settings)
    answer_question(QUESTION, FixedRetriever(), model, settings, trace=record)

    assert model.calls == 2
    assert fake_redis.calls == []
    assert record["cache"] == {"status": "disabled", "source_trace_id": None}


@pytest.mark.parametrize("broken", ["connect", "get", "set"])
def test_cache_failure_never_breaks_answering(
    monkeypatch: pytest.MonkeyPatch, fake_redis: RecordingRedis, broken: str
) -> None:
    import redis

    def fail(*a: Any, **k: Any) -> None:
        raise ConnectionError(f"redis {broken} failed")

    if broken == "connect":
        monkeypatch.setattr(redis.Redis, "from_url", fail)
    else:
        monkeypatch.setattr(fake_redis, broken, fail)

    resp = answer_question(QUESTION, FixedRetriever(), SayingProvider("an answer"), _settings())

    assert resp.answer == "an answer"


def test_redis_client_has_timeouts_so_a_silent_redis_cannot_hang_answering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import redis

    from app.services.cache import AnswerCache

    options: dict[str, Any] = {}

    def from_url(url: str, **kwargs: Any) -> RecordingRedis:
        options.update(kwargs)
        return RecordingRedis()

    monkeypatch.setattr(redis.Redis, "from_url", from_url)
    AnswerCache(_settings())

    assert options["socket_timeout"] > 0
    assert options["socket_connect_timeout"] > 0


def test_cache_hit_points_at_the_trace_that_produced_the_answer(
    fake_redis: RecordingRedis,
) -> None:
    from app.logjson import request_id_var
    from app.trace import new_trace

    settings = _settings()
    model = SayingProvider("an answer")
    records = []
    responses = []
    for request_id in ("request-1", "request-2"):
        token = request_id_var.set(request_id)
        try:
            records.append(new_trace(QUESTION, settings))
            responses.append(answer_question(
                QUESTION, FixedRetriever("3"), model, settings, trace=records[-1]))
        finally:
            request_id_var.reset(token)
    produced, hit = records

    assert model.calls == 1
    assert produced["cache"] == {"status": "miss", "source_trace_id": None}
    assert hit["cache"] == {"status": "hit", "source_trace_id": produced["trace_id"]}
    assert hit["collection"] == {"name": "rahbar-v1", "version": "3"}
    # No model call on a hit: nothing was sent and the model wrote no citations.
    assert hit["context"] == []
    assert hit["citations_raw"] == []
    assert hit["citations"] == produced["citations"]
    # The response belongs to this request, not to the one that filled the cache.
    assert responses[1].trace_id == hit["trace_id"] != produced["trace_id"]
    assert responses[1].request_id == "request-2"


def test_bypass_is_recorded_in_the_trace(fake_redis: RecordingRedis) -> None:
    from app.trace import new_trace

    settings = _settings()
    record = new_trace(QUESTION, settings)
    answer_question(QUESTION, FixedRetriever(), SayingProvider("a"), settings,
                    bypass_cache=True, trace=record)

    assert record["cache"] == {"status": "bypass", "source_trace_id": None}


@pytest.mark.parametrize(("body", "expected"), [
    ({}, False),
    ({"bypass_cache": True}, True),
])
def test_ask_route_passes_the_bypass_flag_to_the_pipeline(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any], expected: bool
) -> None:
    monkeypatch.setenv("RETRIEVER", "stub")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    from app.main import app
    from app.routes import ask

    seen: dict[str, Any] = {}

    def spy(*args: Any, **kwargs: Any) -> AskResponse:
        seen.update(kwargs)
        return answer_question(*args, **kwargs)

    monkeypatch.setattr(ask, "answer_question", spy)
    with TestClient(app) as client:
        resp = client.post("/api/ask", json={"question": QUESTION, **body})

    assert resp.status_code == 200
    assert seen["bypass_cache"] is expected
    assert resp.json()["trace_id"]
