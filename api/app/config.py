"""Runtime configuration, read once from environment variables.

On the cluster these variables come from your ConfigMap and Secret (section 4.1).
Nothing here is baked into the image, so one image runs in every environment.
Add your own keys (relevance floor, cache settings, ...) the same way.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in ("true", "1"):
        return True
    if value in ("false", "0"):
        return False
    # Guessing either way would hide a typo in a ConfigMap, so refuse to start.
    raise ValueError(f"{name} must be true or false, got {raw!r}")


@dataclass(frozen=True)
class Settings:
    # Which implementations to load. "stub" needs no cluster, no model, no network.
    retriever: str = "stub"          # RETRIEVER: stub | chroma
    llm_provider: str = "stub"       # LLM_PROVIDER: stub | ollama-host | ollama-incluster | hosted

    # The embedding pin (section 4.4). The indexer reads the same ConfigMap key.
    embed_model: str = "nomic-embed-text"

    # Vector store
    chroma_host: str = "chroma"
    chroma_port: int = 8000
    collection: str = "rahbar"

    # Release identity. Any change that can alter an answer needs a new RELEASE_ID.
    release_id: str = "v1"           # RELEASE_ID: part of the cache key and of every trace
    prompt_version: str = "p1"       # PROMPT_VERSION: must be a template in prompting.py

    # Retrieval and generation
    top_k: int = 5
    temperature: float = 0.0
    llm_seed: int = 42               # LLM_SEED: sent to the model; the retry uses seed + 1
    llm_model: str = "llama3.2:3b"
    ollama_url: str = "http://host.minikube.internal:11434"
    hosted_base_url: str = ""        # e.g. an OpenAI-compatible endpoint
    llm_api_key: str = field(default="", repr=False)   # from the Secret; never log it

    log_level: str = "INFO"

    # Cache and retrieval tuning (section 3.3)
    redis_url: str = "redis://redis:6379"
    cache_enabled: bool = True       # CACHE_ENABLED: false means Redis is never read or written
    # NOT CHECKED FOR THE CURRENT INDEX. 0.55 predates chunker version 3 and must be
    # re-checked on calibration data (not golden questions) before any baseline.
    # It was chosen from Assignment 02 scores (answerable top-1 0.65-0.78, off-topic 0.50;
    # docs/PIPELINE.md). The new chunker changes chunk text and so the scores: in review, the
    # top-1 score of one CI fixture fee question fell from 0.742 to 0.599.
    relevance_floor: float = 0.55
    # Only the similarity lookup in services/cache.py reads this, and the application does
    # not call that lookup: the cache matches questions exactly (decision D-09, ADR 0008).
    cache_threshold: float = 0.92


def get_settings() -> Settings:
    # Where a default matters for results, read it from the dataclass so it exists once.
    default = Settings()
    return Settings(
        retriever=os.environ.get("RETRIEVER", "stub"),
        llm_provider=os.environ.get("LLM_PROVIDER", "stub"),
        embed_model=os.environ.get("EMBED_MODEL", "nomic-embed-text"),
        chroma_host=os.environ.get("CHROMA_HOST", "chroma"),
        chroma_port=_int("CHROMA_PORT", 8000),
        collection=os.environ.get("CHROMA_COLLECTION", "rahbar"),
        release_id=os.environ.get("RELEASE_ID", default.release_id),
        prompt_version=os.environ.get("PROMPT_VERSION", default.prompt_version),
        top_k=_int("TOP_K", 5),
        temperature=_float("TEMPERATURE", default.temperature),
        llm_seed=_int("LLM_SEED", default.llm_seed),
        llm_model=os.environ.get("LLM_MODEL", "llama3.2:3b"),
        ollama_url=os.environ.get("OLLAMA_URL", "http://host.minikube.internal:11434"),
        hosted_base_url=os.environ.get("HOSTED_BASE_URL", ""),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        redis_url=os.environ.get("REDIS_URL", "redis://redis:6379"),
        cache_enabled=_bool("CACHE_ENABLED", default.cache_enabled),
        relevance_floor=_float("RELEVANCE_FLOOR", default.relevance_floor),
        cache_threshold=_float("CACHE_THRESHOLD", 0.92),
    )
