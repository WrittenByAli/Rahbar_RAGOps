"""Access to Chroma. This module and dummy.py hold the only calls in the whole system
that write to it; sync.py decides what is written (ARCHITECTURE 3.6).

Chroma 1.0.12 behaviour this code relies on: `update` merges chunk metadata;
`collection.modify(metadata=...)` replaces the whole dictionary and refuses
"hnsw:space"; an empty list of ids is an error.
"""

from __future__ import annotations

import uuid
from typing import Any

from .config import IndexerSettings
from .dummy import DUMMY_MODEL

BATCH = 5000


class ModelMismatch(Exception):
    """The collection holds vectors of another embedding model."""


def get_client(settings: IndexerSettings) -> Any:
    import chromadb  # the thin client, from chromadb-client in requirements.txt
    from chromadb.config import Settings as ChromaSettings

    return chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port,
                               settings=ChromaSettings(anonymized_telemetry=False))


def find_collection(client: Any, name: str) -> Any | None:
    """The collection, or None if it does not exist. A server that cannot be reached
    raises: "unreachable" must never be read as "empty"."""
    names = [getattr(item, "name", item) for item in client.list_collections()]
    return client.get_collection(name=name) if name in names else None


def open_collection(client: Any, name: str, embed_model: str, lake_id: str) -> Any:
    """The target collection, created if needed. A collection of another model is never
    rewritten, because a release that may be rolled back to still reads it. The two
    exceptions hold nothing worth keeping: an empty one and the dummy index."""
    collection = find_collection(client, name)
    if collection is not None:
        pinned = (collection.metadata or {}).get("embed_model")
        if pinned == embed_model:
            return collection
        if pinned != DUMMY_MODEL and collection.count() > 0:
            raise ModelMismatch(
                f"collection '{name}' holds vectors of '{pinned}'; it is not rewritten for "
                f"'{embed_model}'. Give the new model its own collection.")
        client.delete_collection(name)
    return client.create_collection(name=name, metadata={
        "hnsw:space": "cosine", "embed_model": embed_model, "lake_id": lake_id,
        "index_generation": uuid.uuid4().hex, "write_in_progress": False})


def bump_generation(client: Any, name: str, **changes: Any) -> None:
    """Give the collection a new index_generation. The API puts it in its cache key, so
    answers cached from an earlier state of the index stop matching. The value is random,
    so a collection that is deleted and created again never repeats one."""
    set_metadata(client, name, **changes, index_generation=uuid.uuid4().hex)


def set_metadata(client: Any, name: str, **changes: Any) -> None:
    """Change some keys of the collection metadata; a value of None removes the key.
    modify() replaces the whole dictionary, so every other key is sent again."""
    collection = client.get_collection(name=name)
    merged = {**(collection.metadata or {}), **changes}
    metadata = {key: value for key, value in merged.items()
                if value is not None
                and key != "hnsw:space"}     # modify() raises on it; the distance stays
    collection.modify(metadata=metadata)


def _slices(total: int) -> list[slice]:
    return [slice(start, start + BATCH) for start in range(0, total, BATCH)]


def upsert(collection: Any, ids: list[str], vectors: list, texts: list[str],
           metadatas: list[dict]) -> None:
    for part in _slices(len(ids)):
        collection.upsert(ids=ids[part], embeddings=vectors[part], documents=texts[part],
                          metadatas=metadatas[part])


def update_metadata(collection: Any, ids: list[str], metadatas: list[dict]) -> None:
    for part in _slices(len(ids)):
        collection.update(ids=ids[part], metadatas=metadatas[part])


def delete_ids(collection: Any, ids: list[str]) -> None:
    for part in _slices(len(ids)):
        collection.delete(ids=ids[part])


def all_metadata(collection: Any, where: dict | None = None) -> dict[str, dict]:
    """{chunk id: metadata} for the chunks that match."""
    found = collection.get(where=where, include=["metadatas"])
    return {chunk_id: meta or {}
            for chunk_id, meta in zip(found["ids"], found["metadatas"])}
