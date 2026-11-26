"""Module level: the embedding model on its own, through Ollama."""

from __future__ import annotations

import time

import httpx

from . import metrics
from .common import embed

MAX_TIMED_CHUNKS = 20
MAX_DETERMINISM_TEXTS = 5
WARMUP_TEXT = "warm-up request, not measured"


def pair_similarities(client: httpx.Client, ollama_url: str, model: str,
                      pairs: list[dict]) -> dict:
    """Mean cosine similarity of the paraphrase pairs and of the unrelated pairs."""
    vectors = embed(client, ollama_url, model, [p[side] for p in pairs for side in ("a", "b")])
    by_type: dict[str, list[float]] = {"paraphrase": [], "unrelated": []}
    rows = []
    for number, pair in enumerate(pairs):
        similarity = metrics.cosine(vectors[2 * number], vectors[2 * number + 1])
        by_type.setdefault(pair["type"], []).append(similarity)
        rows.append({"id": pair.get("id"), "type": pair["type"], "similarity": similarity})
    paraphrase = metrics.mean(by_type["paraphrase"])
    unrelated = metrics.mean(by_type["unrelated"])
    return {
        "paraphrase_pairs": len(by_type["paraphrase"]),
        "unrelated_pairs": len(by_type["unrelated"]),
        "paraphrase_mean": paraphrase,
        "unrelated_mean": unrelated,
        "gap": metrics.difference(paraphrase, unrelated),
        "pair_similarities": rows,
    }


def timing_and_determinism(client: httpx.Client, ollama_url: str, model: str,
                           chunks: list[str]) -> dict:
    """Time per chunk (one chunk per request, after one warm-up request) and the largest
    difference between two embeddings of the same text, also across a model reload.

    `chunks` are real chunk texts, taken from the context of this run's traces. With no
    chunk the values stay None.
    """
    result: dict = {
        "embed_ms_per_chunk": None, "embed_chunks_timed": 0, "embed_batch_size": 1,
        "embed_warmup_requests": 1, "embed_chunk_mean_chars": None,
        "determinism_max_abs_diff": None, "determinism_after_reload_max_abs_diff": None,
        "determinism_texts": 0,
    }
    chunks = chunks[:MAX_TIMED_CHUNKS]
    if not chunks:
        return result
    embed(client, ollama_url, model, [WARMUP_TEXT])
    times, first = [], []
    for text in chunks:
        start = time.perf_counter()
        first.append(embed(client, ollama_url, model, [text])[0])
        times.append((time.perf_counter() - start) * 1000)

    texts = chunks[:MAX_DETERMINISM_TEXTS]
    second = [embed(client, ollama_url, model, [t])[0] for t in texts]
    embed(client, ollama_url, model, [WARMUP_TEXT], keep_alive=0)      # unload the model
    reloaded = [embed(client, ollama_url, model, [t])[0] for t in texts]
    result.update(
        embed_ms_per_chunk=metrics.mean(times),
        embed_chunks_timed=len(chunks),
        embed_chunk_mean_chars=metrics.mean([len(t) for t in chunks]),
        determinism_max_abs_diff=max(
            metrics.max_abs_diff(a, b) for a, b in zip(first, second)),
        determinism_after_reload_max_abs_diff=max(
            metrics.max_abs_diff(a, b) for a, b in zip(first, reloaded)),
        determinism_texts=len(texts),
    )
    return result
