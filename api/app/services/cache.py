"""Answer cache backed by Redis (section 3.3).

The application matches questions EXACTLY, ignoring only letter case and extra white space.
It is not a semantic cache: answer_question() never passes a question vector. The similarity
lookup below came with Assignment 02 and only unit tests reach it (decision D-09,
docs/adr/0008-cache-namespace.md).

Every key carries a namespace: release id, collection, index generation and prompt version,
plus "rail-off" when the retrieval rail is switched off. So a candidate cannot read the live release's answers, and once the index generation changes,
older entries are no longer read (Redis drops them when their time to live ends).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from typing import Any
from urllib.parse import quote

from ..config import Settings
from ..schemas import AskResponse

log = logging.getLogger("rahbar.cache")

TTL_SECONDS = 3600
REDIS_TIMEOUT_SECONDS = 2.0


class AnswerCache:
    def __init__(self, settings: Settings, index_generation: str = "0") -> None:
        import redis
        self.settings = settings
        # typed loosely: redis-py's overloaded stubs otherwise infer Awaitable here
        self.redis: Any = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            # without time limits, a Redis that stops replying would hang the request
            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
            socket_timeout=REDIS_TIMEOUT_SECONDS,
        )
        self.threshold = settings.cache_threshold
        parts = [settings.release_id, settings.collection, index_generation,
                 settings.prompt_version]
        if not settings.retrieval_rail_enabled:
            # An answer made without the rail must never be served where the rail is on.
            parts.append("rail-off")
        # Percent-encoding keeps the key readable, and stops a ":" inside one part from moving
        # the boundary between parts, or a "*" from acting as a wildcard in scan_iter.
        self.namespace = ":".join(quote(part, safe="") for part in parts)

    @staticmethod
    def _question_hash(question: str) -> str:
        normalised = " ".join(question.split()).lower()
        return hashlib.sha256(normalised.encode()).hexdigest()

    def _hash_key(self, question: str) -> str:
        return f"cache:{self.namespace}:{self._question_hash(question)}"

    def _vec_key(self, question: str) -> str:
        return f"vec:{self.namespace}:{self._question_hash(question)}"

    def get(self, question: str, question_vec: list[float] | None = None) -> AskResponse | None:
        """The stored response, or None.

        Its trace_id is that of the request that produced the answer (the source trace id).
        """
        exact_key = self._hash_key(question)
        data = self.redis.get(exact_key)
        if data:
            log.info("cache hit (exact)", extra={"question": question[:60]})
            return AskResponse.model_validate_json(data)

        if question_vec:
            for key in self.redis.scan_iter(f"vec:{self.namespace}:*"):
                stored = self.redis.get(key)
                if not stored:
                    continue
                entry = json.loads(stored)
                stored_vec = entry.get("vector", [])
                sim = self._cosine_similarity(question_vec, stored_vec)
                if sim >= self.threshold:
                    cache_key = entry.get("cache_key", "")
                    cached_data = self.redis.get(cache_key)
                    if cached_data:
                        log.info("cache hit (semantic)", extra={"similarity": round(sim, 4)})
                        return AskResponse.model_validate_json(cached_data)
        return None

    @staticmethod
    def _should_skip_cache(response: AskResponse) -> bool:
        """never_cache abstentions or follow-up answers."""
        return not response.answered_from_context

    def put(self, question: str, response: AskResponse, question_vec: list[float] | None = None) -> None:
        """Store a response, including its trace_id, which a later hit reports as its source."""
        if self._should_skip_cache(response):
            return
        key = self._hash_key(question)
        self.redis.set(key, response.model_dump_json(), ex=TTL_SECONDS)
        if question_vec:
            vec_key = self._vec_key(question)
            self.redis.set(
                vec_key, json.dumps({"vector": question_vec, "cache_key": key}), ex=TTL_SECONDS
            )

    def flush(self) -> None:
        """Delete every entry in every namespace."""
        for key in self.redis.scan_iter("cache:*"):
            self.redis.delete(key)
        for key in self.redis.scan_iter("vec:*"):
            self.redis.delete(key)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)


# The Assignment 02 name, still imported by its unit tests. See the module docstring.
SemanticCache = AnswerCache
