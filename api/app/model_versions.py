"""Which model weights really ran: the digest Ollama reports for a model.

A tag such as "qwen2.5:3b" can be pulled again and then name different weights, so a trace
records the digest from GET /api/tags as the model's version. The digest is remembered for a
short time: most requests cost no extra call, and a re-pulled model still shows up.
"""

from __future__ import annotations

import logging
import time

import httpx

from .config import Settings

log = logging.getLogger("rahbar.model_versions")

STUB_VERSION = "stub"              # stub mode runs no model, so there is no digest to read
TTL_SECONDS = 60.0
FAILURE_TTL_SECONDS = 10.0         # so an unreachable Ollama does not delay every request
TIMEOUT_SECONDS = 2.0

# (Ollama URL, model) -> (when this entry expires, digest). No lock: if two requests miss at
# once, both ask Ollama and both store the same answer.
_cache: dict[tuple[str, str], tuple[float, str | None]] = {}


def clear_cache() -> None:
    _cache.clear()


def ollama_digest(ollama_url: str, model: str) -> str | None:
    """The model's digest, or None when Ollama cannot tell. It never raises."""
    cached = _cache.get((ollama_url, model))
    if cached and time.monotonic() < cached[0]:
        return cached[1]
    try:
        digest = _read_digest(ollama_url, model)
    except Exception as exc:  # noqa: BLE001 - tracing must never break answering
        log.warning("model digest unavailable", extra={"model": model, "error": str(exc)})
        digest = None
    lifetime = TTL_SECONDS if digest else FAILURE_TTL_SECONDS
    _cache[(ollama_url, model)] = (time.monotonic() + lifetime, digest)
    return digest


def _read_digest(ollama_url: str, model: str) -> str | None:
    response = httpx.get(f"{ollama_url}/api/tags", timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    # Ollama lists "nomic-embed-text:latest" for a model that is configured without a tag.
    wanted = model if ":" in model else f"{model}:latest"
    for entry in response.json()["models"]:
        if wanted in (entry.get("name"), entry.get("model")):
            return entry.get("digest") or None
    return None


def embedding_version(settings: Settings) -> str | None:
    if settings.retriever == "stub":
        return STUB_VERSION
    return ollama_digest(settings.ollama_url, settings.embed_model)


def llm_version(settings: Settings) -> str | None:
    if settings.llm_provider == "stub":
        return STUB_VERSION
    if settings.llm_provider == "hosted":
        return None                # a hosted API reports no digest for its model
    return ollama_digest(settings.ollama_url, settings.llm_model)
