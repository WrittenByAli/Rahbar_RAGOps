"""Stage 4: Embed. One vector per chunk, from the pinned model only."""

from __future__ import annotations

import httpx

from .config import IndexerSettings

BATCH_SIZE = 64


def model_digest(model: str, settings: IndexerSettings) -> str:
    """The digest Ollama reports for the model. A tag such as "latest" can start to mean
    other weights; the digest shows it, and the chunks are then embedded again."""
    response = httpx.get(f"{settings.ollama_url}/api/tags", timeout=30.0)
    response.raise_for_status()
    for entry in response.json()["models"]:
        if entry["name"] in (model, f"{model}:latest"):
            return str(entry["digest"])
    # No stand-in value: an unknown digest would look like a changed model.
    raise LookupError(f"Ollama has no model '{model}'; run: ollama pull {model}")


def embed(texts: list[str], settings: IndexerSettings) -> list[list[float]]:
    """Embed with settings.embed_model via Ollama's /api/embed endpoint.

    Batches texts into groups to avoid overwhelming the server.
    """
    all_vectors: list[list[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        response = httpx.post(
            f"{settings.ollama_url}/api/embed",
            json={"model": settings.embed_model, "input": batch},
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()
        embeddings = data["embeddings"]
        all_vectors.extend(embeddings)

    return all_vectors
