"""Commands on documents already in the lake: delete, restore, count and status.

delete and restore write, so they take the ingestion lock. Their proofs hold counts and
versions read back from the vector store, not from the manifest (CONTRACTS).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from . import store, sync
from .config import IndexerSettings, targets
from .ingest import Services, build_quarantine_proof, lake_versions, open_lake
from .lake import Lake
from .lock import IngestLock
from .report import Reports, new_run_id, utc_now


def delete_document(settings: IndexerSettings, services: Services,
                    doc_id: str) -> dict[str, Any]:
    """Remove every version of a document from the lake and from every target (A2.3)."""
    target_list = targets(settings)
    lake = Lake(Path(settings.lake_dir))
    with IngestLock(lake.lock_path, settings.lock_stale_seconds) as lock:
        open_lake(lake)
        run_id, started = new_run_id(), utc_now()
        before = {t.collection: len(sync.chunks_in(services.client, t.collection,
                                                   {"doc_id": doc_id}))
                  for t in target_list}
        in_lake = doc_id in lake.manifest["documents"]
        if not in_lake and not any(before.values()):
            raise LookupError(f"no document '{doc_id}' in the lake or in any target")
        previous_state = lake.state_id()
        if in_lake:
            lake.delete(doc_id, run_id)
            lake.save()     # first: if the run dies now, the next sweep finishes the job

        keys = {(d, v) for d, v, _ in lake.versions()}
        results = []
        for target in target_list:
            lock.check()
            sync.remove_document(services.client, target.collection, doc_id,
                                 previous_state, lake.state_id())
            results.append({
                "collection": target.collection,
                "count_before": before[target.collection],
                "count_after": len(sync.chunks_in(services.client, target.collection,
                                                  {"doc_id": doc_id})),
                "orphan_chunks_after": sync.count_orphans(services.client,
                                                          target.collection, keys)})
        # The first target (the live collection) is the one the contract names.
        proof = {"run_id": run_id, "started_at": started, "doc_id": doc_id,
                 **results[0], "targets": results}
        Reports(settings.reports_path).proof("deleted", proof)
    return proof


def restore_version(settings: IndexerSettings, services: Services, doc_id: str,
                    version: str) -> dict[str, Any]:
    """Roll a document back to an archived version (A2.4). Only metadata changes in the
    collections, so nothing is embedded."""
    target_list = targets(settings)
    primary = target_list[0].collection
    lake = Lake(Path(settings.lake_dir))
    with IngestLock(lake.lock_path, settings.lock_stale_seconds) as lock:
        open_lake(lake)
        run_id, started = new_run_id(), utc_now()
        previous_state = lake.state_id()
        replaced = lake.restore(doc_id, version)
        retrieved_before = sync.retrievable_versions(services.client, primary, doc_id)
        lake.save()
        lake.sync_folders()

        versions = lake_versions(lake)
        results: list[dict[str, Any]] = []
        for target in target_list:
            counts = sync.reconcile(services.client, target, versions, lake.lake_id,
                                    lake.state_id(), services.embed,
                                    services.model_digest, lock.check, only_doc=doc_id,
                                    previous_state_id=previous_state)
            results.append({"collection": target.collection, "counts": counts,
                            "retrieved_versions": sync.retrievable_versions(
                                services.client, target.collection, doc_id)})
        proof = {
            "run_id": run_id, "started_at": started, "doc_id": doc_id,
            "collection": primary, "restored_version": version,
            "replaced_version": replaced,
            "retrieved_versions_before": retrieved_before,
            "retrieved_versions_after": results[0]["retrieved_versions"],
            "chunks_embedded": sum(r["counts"]["chunks_embedded"] for r in results),
            "lake_state_id": lake.state_id(), "targets": results}
        Reports(settings.reports_path).proof("restored", proof)
    return proof


def count_chunks(settings: IndexerSettings, services: Services,
                 doc_id: str) -> dict[str, dict[str, Any]]:
    """Chunks of one document in each target, counted in the vector store."""
    found = {}
    for target in targets(settings):
        chunks = sync.chunks_in(services.client, target.collection, {"doc_id": doc_id})
        found[target.collection] = {
            "total": len(chunks),
            "by_version": dict(sorted(Counter(m["doc_version"]
                                              for m in chunks.values()).items()))}
    return found


def status(settings: IndexerSettings, services: Services) -> dict[str, Any]:
    """The lake and the targets as they are now. Reads only."""
    lake = Lake(Path(settings.lake_dir))
    lake.load()
    collections = {}
    for target in targets(settings):
        chunks = sync.chunks_in(services.client, target.collection)
        collection = store.find_collection(services.client, target.collection)
        metadata = (collection.metadata or {}) if collection else {}
        collections[target.collection] = {
            "chunks": len(chunks),
            "chunks_by_doc_id": dict(sorted(Counter(
                m.get("doc_id", "") for m in chunks.values()).items())),
            "embed_model": metadata.get("embed_model"),
            "lake_state_id": metadata.get("lake_state_id"),
            "index_generation": metadata.get("index_generation"),
            "write_in_progress": metadata.get("write_in_progress")}
    return {
        "lake_dir": settings.lake_dir, "lake_state_id": lake.state_id(),
        "documents": {doc_id: {
            "pinned": document["pinned"],
            "versions": {v: e["status"] for v, e in sorted(document["versions"].items())}}
            for doc_id, document in sorted(lake.manifest["documents"].items())},
        "deleted": [d["doc_id"] for d in lake.manifest["deleted"]],
        "quarantined_files": len(lake.quarantined()),
        "collections": collections}


def quarantine_proof(settings: IndexerSettings, services: Services) -> dict[str, Any]:
    """Write proofs/quarantine.json again from the quarantine folder and the store."""
    lake = Lake(Path(settings.lake_dir))
    lake.load()
    proof = {"run_id": new_run_id(), "started_at": utc_now(),
             **build_quarantine_proof(lake, services.client, targets(settings))}
    Reports(settings.reports_path).proof("quarantine", proof)
    return proof
