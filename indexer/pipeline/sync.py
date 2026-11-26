"""Stages 4 and 5: make one collection match the data lake (A2, ARCHITECTURE 3.6).

For every version in the lake: chunk it, compare with what the collection already holds
for that (doc_id, version), and write only the difference. The collection describes
itself through chunk metadata, so there is no second ledger that could disagree with it,
and a run that was interrupted is repaired by the next one.

Everything is planned from reads first. If nothing differs, nothing is written. If
something differs, new chunks are embedded, the collection's index_generation changes,
the writes happen, and the generation changes again.

The collection metadata "lake_state_id" names the set of active documents the collection
was built from. Two collections with the same value hold the same documents. It is
removed before the first write and set again after the last one, so it never describes
a half-written collection.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import store
from .chunk import CHUNKER_VERSION, chunk
from .clean import CLEANER_VERSION, clean
from .config import Target
from .extract import EXTRACTOR_VERSION
from .models import Chunk, Document

Embed = Callable[[list[str], str], list[list[float]]]
Key = tuple[str, str]                       # (doc_id, doc_version)


class ForeignCollection(Exception):
    """This run would delete chunks that another data lake wrote."""


@dataclass
class LakeVersion:
    """One document version that the collection must hold."""

    document: Document                      # superseded is already set
    content_sha256: str

    @property
    def key(self) -> Key:
        return (self.document.doc_id, self.document.doc_version)


Expected = list[tuple[Chunk, dict[str, Any]]]


@dataclass
class Plan:
    """What has to change for one version."""

    embed: Expected = field(default_factory=list)           # embed and upsert
    update: list[tuple[str, dict]] = field(default_factory=list)   # metadata only
    delete: list[str] = field(default_factory=list)         # stale ids
    outcome: str = "unchanged"              # new, changed, superseded or unchanged
    newly_superseded: bool = False

    @property
    def writes(self) -> bool:
        return bool(self.embed or self.update or self.delete)


def fingerprint(target: Target, model_digest: str) -> str:
    """Everything besides the text that decides a chunk and its vector. Chunk ids do not
    include it, so a different fingerprint means: embed every chunk again."""
    parts = [EXTRACTOR_VERSION, CHUNKER_VERSION, CLEANER_VERSION, target.chunk_size,
             target.chunk_overlap, target.embed_model, model_digest]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def expected_chunks(version: LakeVersion, target: Target, stamp: str) -> Expected:
    chunks = chunk(clean(version.document), target.chunk_size, target.chunk_overlap)
    status = "superseded" if version.document.superseded else "active"
    return [(c, {**c.metadata(), "content_sha256": version.content_sha256,
                 "fingerprint": stamp, "status": status, "chunk_count": len(chunks)})
            for c in chunks]


def plan_version(stored: dict[str, dict], expected: Expected, superseded: bool) -> Plan:
    """Compare the stored chunks of one version with the chunks it should have."""
    plan = Plan()
    wanted = {c.id for c, _ in expected}
    plan.delete = sorted(set(stored) - wanted)
    # One stored chunk with another fingerprint is enough to embed the whole version again.
    stale_vectors = bool(expected) and any(
        meta.get("fingerprint") != expected[0][1]["fingerprint"] for meta in stored.values())
    for c, metadata in expected:
        if stale_vectors or c.id not in stored:
            plan.embed.append((c, metadata))
        elif any(stored[c.id].get(k) != v for k, v in metadata.items()):
            plan.update.append((c.id, metadata))
    plan.newly_superseded = superseded and (
        not stored or any(meta.get("superseded") is False for meta in stored.values()))
    if not stored:
        plan.outcome = "new"
    elif plan.embed or plan.delete:
        plan.outcome = "changed"
    elif plan.update:
        plan.outcome = "superseded" if plan.newly_superseded else "changed"
    return plan


def _matches_state(metadata: dict, state_id: str | None) -> bool:
    """Whether the collection was completely built from that lake state."""
    return (state_id is not None and metadata.get("lake_state_id") == state_id
            and not metadata.get("write_in_progress"))


def reconcile(client: Any, target: Target, versions: list[LakeVersion], lake_id: str,
              lake_state_id: str, embed: Embed, model_digest: Callable[[str], str],
              check_lock: Callable[[], None], only_doc: str | None = None,
              previous_state_id: str | None = None,
              adopt: bool = False) -> dict[str, int]:
    """Write the difference between `versions` and the collection, and return the counts.

    With only_doc, just that document is looked at. Otherwise the whole collection is,
    and chunks that no lake version owns are swept away (orphans). lake_state_id is
    recorded on the collection; after looking at one document only, that is justified
    only if the collection matched previous_state_id before."""
    collection = store.open_collection(client, target.collection, target.embed_model,
                                       lake_id)
    stamp = fingerprint(target, model_digest(target.embed_model))
    stored: dict[Key, dict[str, dict]] = defaultdict(dict)
    where = {"doc_id": only_doc} if only_doc else None
    for chunk_id, meta in store.all_metadata(collection, where).items():
        stored[(meta.get("doc_id", ""), meta.get("doc_version", ""))][chunk_id] = meta

    wanted = [v for v in versions if only_doc in (None, v.document.doc_id)]
    plans = [plan_version(stored.get(v.key, {}), expected_chunks(v, target, stamp),
                          v.document.superseded) for v in wanted]
    orphan_keys = set(stored) - {v.key for v in versions}
    orphans = sorted(i for key in orphan_keys for i in stored[key])

    metadata = collection.metadata or {}
    # Another lake's collection, or one with no owner on record (the Assignment 02 index).
    not_ours = metadata.get("lake_id") != lake_id
    if orphans and not_ours and not adopt:
        raise ForeignCollection(
            f"collection '{target.collection}' was not written by this data lake "
            f"(lake_id: {metadata.get('lake_id')}), and {len(orphans)} of its chunks are "
            "not in it. Nothing was changed. Check LAKE_DIR; to let this lake take the "
            "collection over, use --adopt-collection.")

    claim = (lake_state_id if only_doc is None
             or _matches_state(metadata, previous_state_id) else None)
    counts = _count(wanted, plans, orphans, orphan_keys)
    must_write = bool(orphans or not_ours or any(p.writes for p in plans))
    # write_in_progress: an earlier run died between its first and last write. Its
    # generation must still be replaced, even if there is nothing left to write.
    if not must_write and not metadata.get("write_in_progress"):
        if metadata.get("lake_state_id") != claim:      # no answer changes: same generation
            check_lock()
            store.set_metadata(client, target.collection, lake_state_id=claim)
        return counts

    new = [pair for plan in plans for pair in plan.embed]
    vectors = embed([c.text for c, _ in new], target.embed_model) if new else []
    if len(vectors) != len(new):
        raise RuntimeError(f"got {len(vectors)} vectors for {len(new)} chunks")

    check_lock()
    store.bump_generation(client, target.collection, write_in_progress=True, lake_id=lake_id,
                          lake_state_id=None)
    collection = client.get_collection(name=target.collection)
    vector_of = {c.id: vector for (c, _), vector in zip(new, vectors)}
    # Across all documents: every new chunk first, then metadata (which is what hides a
    # superseded version), then deletes. If a write fails, nothing that was retrievable
    # has been hidden or removed before its replacement is there.
    for plan in plans:
        if plan.embed:
            check_lock()
            store.upsert(collection, [c.id for c, _ in plan.embed],
                         [vector_of[c.id] for c, _ in plan.embed],
                         [c.text for c, _ in plan.embed], [m for _, m in plan.embed])
    for plan in plans:
        if plan.update:
            check_lock()
            store.update_metadata(collection, [i for i, _ in plan.update],
                                  [m for _, m in plan.update])
    stale = [i for plan in plans for i in plan.delete] + orphans
    if stale:
        check_lock()
        store.delete_ids(collection, stale)
    check_lock()
    dimension = {"dimension": len(vectors[0])} if vectors else {}
    store.bump_generation(client, target.collection, write_in_progress=False,
                          lake_id=lake_id, lake_state_id=claim, **dimension)
    return counts


def _count(wanted: list[LakeVersion], plans: list[Plan], orphans: list[str],
           orphan_keys: set[Key]) -> dict[str, int]:
    """A "document" here is one version of a document."""
    def how_many(outcome: str) -> int:
        return sum(1 for p in plans if p.outcome == outcome)

    embedded = sum(len(p.embed) for p in plans)
    return {
        "documents_scanned": len(wanted),
        "documents_new": how_many("new"),
        "documents_changed": how_many("changed"),
        "documents_unchanged": how_many("unchanged"),
        "documents_deleted": len(orphan_keys),
        "documents_superseded": sum(1 for p in plans if p.newly_superseded),
        "chunks_embedded": embedded,
        "chunks_upserted": embedded,
        "chunks_deleted": sum(len(p.delete) for p in plans) + len(orphans),
        "orphan_chunks": len(orphans),
    }


# ---------------------------------------------------------------- delete, and reading back
def remove_document(client: Any, name: str, doc_id: str, previous_state_id: str,
                    lake_state_id: str) -> None:
    """Delete every chunk of every version of a document from one collection. It gets
    the new lake_state_id only if it matched the state before the delete."""
    collection = store.find_collection(client, name)
    if collection is None:
        return
    metadata = collection.metadata or {}
    claim = lake_state_id if _matches_state(metadata, previous_state_id) else None
    if not store.all_metadata(collection, {"doc_id": doc_id}):
        if metadata.get("lake_state_id") != claim:
            store.set_metadata(client, name, lake_state_id=claim)
        return
    store.bump_generation(client, name, write_in_progress=True, lake_state_id=None)
    client.get_collection(name=name).delete(where={"doc_id": doc_id})
    store.bump_generation(client, name, write_in_progress=False, lake_state_id=claim)


def chunks_in(client: Any, name: str, where: dict | None = None) -> dict[str, dict]:
    """{chunk id: metadata} read from the vector store; empty if there is no collection."""
    collection = store.find_collection(client, name)
    return {} if collection is None else store.all_metadata(collection, where)


def ids_by_document(client: Any, name: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    for chunk_id, meta in chunks_in(client, name).items():
        found[meta.get("doc_id", "")].append(chunk_id)
    return {doc_id: sorted(ids) for doc_id, ids in sorted(found.items())}


def retrievable_versions(client: Any, name: str, doc_id: str) -> list[str]:
    """The versions the API can retrieve, read with its own filter, superseded=False."""
    where = {"$and": [{"doc_id": doc_id}, {"superseded": False}]}
    return sorted({m["doc_version"] for m in chunks_in(client, name, where).values()})


def count_orphans(client: Any, name: str, keys: set[Key]) -> int:
    return sum(1 for meta in chunks_in(client, name).values()
               if (meta.get("doc_id", ""), meta.get("doc_version", "")) not in keys)
