"""The real retriever (sections 3.2 and 4.4)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from ..config import Settings
from ..schemas import Passage


def _number(value: Any) -> int | None:
    """A page number from metadata. bool is a subclass of int, so it is excluded."""
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


class ChromaRetriever:
    name = "chroma"
    # The store applies this in every query (superseded versions stay in the collection), and
    # the trace copies it into filters_applied, so the two cannot disagree.
    filters: ClassVar[dict[str, Any]] = {"superseded": False}

    def __init__(self, settings: Settings) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.settings = settings
        self.client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port,
                                          settings=ChromaSettings(anonymized_telemetry=False))

    def index_generation(self) -> str:
        """The collection metadata value the ingestion Job bumps when it changes the index.

        "0" when the collection does not have it. It is fetched from the server on every
        call, so cached answers stop matching as soon as it changes.
        """
        collection = self.client.get_collection(name=self.settings.collection)
        return str((collection.metadata or {}).get("index_generation", "0"))

    def retrieve(self, question: str, k: int) -> list[Passage]:
        return self.search(self.embed_query(question), k)

    def embed_query(self, question: str) -> list[float]:
        """The question's vector. Apart from search(), so each can be timed on its own."""
        import httpx
        resp = httpx.post(
            f"{self.settings.ollama_url}/api/embed",
            json={"model": self.settings.embed_model, "input": [question]},
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["embeddings"][0]

    def search(self, vector: Sequence[float], k: int) -> list[Passage]:
        """Up to k chunks nearest to the vector, best first."""
        collection = self.client.get_collection(name=self.settings.collection)
        results = collection.query(
            query_embeddings=[vector],
            n_results=k,
            where=dict(self.filters),
        )

        passages = []
        if results and results["ids"] and results["ids"][0]:
            for i, chunk_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results["distances"] else 0.0
                score = 1.0 - distance
                meta = (results["metadatas"][0][i] if results["metadatas"] else None) or {}
                text = results["documents"][0][i] if results["documents"] else ""
                passages.append(Passage(
                    chunk_id=chunk_id,
                    text=str(text),
                    source=str(meta.get("source", "")),
                    section=str(meta.get("section", "")),
                    page=_number(meta.get("page")),
                    score=score,
                    doc_id=_text(meta.get("doc_id")),
                    doc_version=_text(meta.get("doc_version")),
                    page_start=_number(meta.get("page_start")),
                    page_end=_number(meta.get("page_end")),
                ))
        return passages
