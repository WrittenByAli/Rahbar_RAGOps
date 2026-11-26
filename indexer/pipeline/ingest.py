"""One ingestion run (A1, A2), in the order of ARCHITECTURE 3.2:

    extract -> clean -> VERIFY -> update the lake -> chunk -> embed -> upsert and delete

Nothing is moved or written to the vector store until every new file has been verified.
New files come from the lake's inbox (and leave it, for the lake or for quarantine) and
from CORPUS_DIR or --source folders, which are only read.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import store, sync
from .clean import CLEANER_VERSION, clean
from .config import IndexerSettings, Target, targets
from .embed import embed, model_digest
from .extract import EXTRACTOR_VERSION, extract
from .lake import ACTIVE, Lake
from .lock import IngestLock
from .metadata import (SIDECAR_SUFFIX, Metadata, derive_doc_id, front_matter, resolve,
                       sidecar_path, valid_text)
from .models import Document, Page
from .neardup import WindowVectors
from .report import Reports, new_run_id, utc_now
from .verify import (Candidate, Known, content_hash, magic_problem, normalised_text,
                     truncation_signals, verify_batch)

log = logging.getLogger("rahbar.indexer")


@dataclass
class Services:
    """The outside world, passed in so that tests can replace it."""

    client: Any                                              # a Chroma client
    embed: Callable[[list[str], str], list[list[float]]]     # (texts, model)
    model_digest: Callable[[str], str]                       # (model)


def real_services(settings: IndexerSettings) -> Services:
    return Services(
        client=store.get_client(settings),
        embed=lambda texts, model: embed(texts, replace(settings, embed_model=model)),
        model_digest=lambda model: model_digest(model, settings))


@dataclass
class IngestResult:
    run_id: str
    verification: dict[str, Any]
    ingest: list[dict[str, Any]] = field(default_factory=list)      # one per target
    problems: list[str] = field(default_factory=list)       # a requested proof not holding


class EmptyLake(Exception):
    """The lake has never held a document, so no collection is touched."""

    def __init__(self, result: IngestResult) -> None:
        quarantined = sum(1 for e in result.verification["documents"]
                          if e["decision"] == "quarantined")
        super().__init__(f"the data lake holds no document, nothing to index "
                         f"({quarantined} file(s) quarantined in this run)")
        self.result = result


# ---------------------------------------------------------------- finding new files
@dataclass(frozen=True)
class Input:
    path: Path
    batch: str
    consume: bool            # True: from the inbox, removed once it has been handled


def discover(corpus_dir: str) -> list[tuple[Path, str, bool]]:
    """List (path, doc_version, superseded) for the documents of a folder.

    corpus/fetch.py writes _index.json next to the files it downloads; use it when
    present. Otherwise read Markdown front matter (the CI fixtures work this way).
    Ingestion uses only the paths: versions come from metadata, and which version is
    superseded follows from the versions in the lake.
    """
    root = Path(corpus_dir)
    index = root / "_index.json"
    if index.exists():
        entries = json.loads(index.read_text(encoding="utf-8"))
        return [(root / e["file"], str(e.get("doc_version", "unknown")),
                 bool(e.get("superseded", False))) for e in entries]
    found = []
    for path in sorted(root.glob("*")):
        if path.suffix.lower() not in {".pdf", ".md", ".html", ".txt"}:
            continue
        if path.name.lower().startswith("readme"):
            continue
        meta = _listing_front_matter(path) if path.suffix.lower() == ".md" else {}
        found.append((path, str(meta.get("doc_version", "unknown")),
                      str(meta.get("superseded", "false")).lower() == "true"))
    return found


def _listing_front_matter(path: Path) -> dict:
    """For discover() only. A file that cannot be read is still listed: verification
    gives it its reason."""
    try:
        return front_matter(path.read_text(encoding="utf-8-sig", errors="replace"))[0]
    except OSError:
        return {}


def _inbox_files(inbox: Path) -> list[Input]:
    """inbox/<batch>/<file>, or a file directly in the inbox. Every file is a delivery,
    also one of a type we cannot read: it is quarantined with that reason."""
    found = []
    for entry in sorted(inbox.glob("*")):
        files = sorted(entry.iterdir()) if entry.is_dir() else [entry]
        batch = entry.name if entry.is_dir() else "inbox"
        for path in files:
            name = path.name.lower()
            if path.is_dir():
                log.warning("ignored a folder inside a batch: %s", path)
            elif not (name.startswith((".", "readme")) or name.endswith(SIDECAR_SUFFIX)):
                found.append(Input(path, batch, consume=True))
    return found


def find_inputs(settings: IndexerSettings, lake: Lake, sources: list[Path] | None,
                batch: str | None) -> list[Input]:
    if sources:
        folders, found = list(sources), []
        for folder in folders:
            if not folder.is_dir():
                raise FileNotFoundError(f"source folder {folder} does not exist")
    else:
        found = _inbox_files(lake.inbox)
        corpus = Path(settings.corpus_dir) if settings.corpus_dir else None
        folders = [corpus] if corpus and corpus.is_dir() else []
    for folder in folders:
        found += [Input(path, folder.name, consume=False)
                  for path, _, _ in discover(str(folder))]
    return [replace(i, batch=batch) for i in found] if batch else found


# ---------------------------------------------------------------- reading one file
def _cleaned_text(doc_id: str, version: str, pages: list[Page]) -> str:
    document = Document(source=doc_id, doc_version=version, superseded=False, pages=pages)
    return normalised_text(clean(document).pages)


def load_candidate(lake: Lake, item: Input) -> Candidate:
    """Read a file and extract its text. Whatever goes wrong becomes a fact that the
    checks judge; one bad file never stops the run."""
    try:
        return _read_candidate(lake, item)
    except Exception as exc:  # noqa: BLE001 - quarantine this file, go on with the others
        log.warning("could not process %s", valid_text(item.path.name), exc_info=True)
        try:
            raw = hashlib.sha256(item.path.read_bytes()).hexdigest()
        except OSError:
            raw = ""
        name = valid_text(item.path.name)
        meta = Metadata(doc_id=derive_doc_id(name, raw or "unreadable"),
                        sources={"doc_id": "derived"})
        return Candidate(
            file=name, batch=item.batch, path=item.path, raw_sha256=raw, size_bytes=0,
            meta=meta, pages=[], text="", content_sha256="", read_only=not item.consume,
            file_problem=valid_text(f"could not be processed: {type(exc).__name__}: "
                                    f"{str(exc)[:200]}"))


def _read_candidate(lake: Lake, item: Input) -> Candidate:
    data = item.path.read_bytes()
    raw = hashlib.sha256(data).hexdigest()
    meta = resolve(item.path, raw)
    suffix = item.path.suffix.lower()
    problem = magic_problem(suffix, data)
    pages: list[Page] = []
    if not problem:
        cached = lake.cached_extraction(raw, suffix)
        try:
            pages = cached or extract(str(item.path), meta.doc_version, False).pages
        except Exception as exc:  # noqa: BLE001 - a parser error is a quality failure
            problem = f"extraction failed: {type(exc).__name__}: {str(exc)[:200]}"
        else:
            if not pages:
                problem = "extraction failed: no page could be read"
            # Text with an OCR error is not cached: a later run may be able to OCR it.
            elif cached is None and not any(p.ocr_error for p in pages):
                pages = [replace(p, text=valid_text(p.text)) for p in pages]
                lake.cache_extraction(raw, suffix, pages)
    name = valid_text(item.path.name)
    pages = [replace(page, source=name, text=valid_text(page.text)) for page in pages]
    text = _cleaned_text(meta.doc_id, meta.doc_version, pages)
    return Candidate(
        file=name, batch=item.batch, path=item.path, raw_sha256=raw,
        read_only=not item.consume,
        size_bytes=len(data), meta=meta, pages=pages, text=text,
        content_sha256=content_hash(text), file_problem=problem,
        truncation=truncation_signals(suffix, data) if data else [])


# ---------------------------------------------------------------- what the lake holds
def known_versions(lake: Lake) -> list[Known]:
    found = []
    for doc_id, version, entry in lake.versions():
        found.append(Known(
            doc_id=doc_id, version=version, file=entry["file"], title=entry["title"],
            raw_sha256=entry["raw_sha256"], content_sha256=entry["content_sha256"],
            status=entry["status"],
            text=_cleaned_text(doc_id, version, lake.pages(doc_id, version))))
    return found


def lake_versions(lake: Lake) -> list[sync.LakeVersion]:
    """Every version the target collections must hold, superseded ones included."""
    return [sync.LakeVersion(
        document=Document(source=entry["file"], doc_version=version, doc_id=doc_id,
                          superseded=entry["status"] != ACTIVE,
                          pages=lake.pages(doc_id, version)),
        content_sha256=entry["content_sha256"])
        for doc_id, version, entry in lake.versions()]


def _extract_again(lake: Lake, doc_id: str, version: str) -> None:
    """Run today's extractor on the original kept in the lake. If that goes worse than
    before (an error, or more pages that could not be OCR'd), keep the old text; the
    version stays marked old, so a later run tries again."""
    entry = lake.manifest["documents"][doc_id]["versions"][version]
    original = lake.folder(doc_id, version, entry["status"]) / entry["file"]
    suffix = original.suffix.lower()
    try:
        failed_before = sum(1 for p in lake.pages(doc_id, version) if p.ocr_error)
        pages = (lake.cached_extraction(entry["raw_sha256"], suffix)
                 or extract(str(original), version, False).pages)
    except Exception as exc:  # noqa: BLE001 - the verified old text must stay usable
        log.warning("%s %s not extracted again, keeping the old text: %s",
                    doc_id, version, exc)
        return
    failed_now = sum(1 for p in pages if p.ocr_error)
    if not pages or failed_now > failed_before:
        log.warning("%s %s not extracted again, keeping the old text: %d page(s) failed",
                    doc_id, version, failed_now)
        return
    if not failed_now:
        lake.cache_extraction(entry["raw_sha256"], suffix, pages)
    lake.replace_pages(doc_id, version, pages)


def open_lake(lake: Lake) -> None:
    """Load the manifest and repair what an interrupted or older run left behind."""
    lake.load()
    lake.sync_folders()
    for doc_id, version, entry in list(lake.versions()):
        if entry.get("extractor_version") != EXTRACTOR_VERSION:
            _extract_again(lake, doc_id, version)
    lake.refresh_content_hashes(
        lambda doc_id, version: content_hash(
            _cleaned_text(doc_id, version, lake.pages(doc_id, version))),
        CLEANER_VERSION)


# ---------------------------------------------------------------- proofs read from Chroma
def build_quarantine_proof(lake: Lake, client: Any,
                           target_list: list[Target]) -> dict[str, Any]:
    """For each quarantined file: is it in quarantine, and how many chunks of its text
    are in the target collections? The count is read from the vector store.

    One case is listed apart, under "already_indexed", with its real count: the text had
    been accepted by an earlier run, and every chunk of it now stored was already in the
    store when the rejected file arrived. Those chunks are not from that file."""
    stored = {t.collection: sync.chunks_in(client, t.collection) for t in target_list}
    accepted = {(doc_id, e["content_sha256"]): (e["run_id"], e["file"])
                for doc_id, _, e in lake.versions()}
    documents: list[dict] = []
    apart: list[dict] = []
    resolved: list[dict] = []
    for folder, why in lake.quarantined():
        run, source = accepted.get((why["doc_id"], why["content_sha256"]), ("", ""))
        if run > why["run_id"]:
            # A later delivery of this text passed (say, with a corrected sidecar).
            resolved.append({"file": why["file"], "doc_id": why["doc_id"],
                             "quarantined_in": why["run_id"], "accepted_in": run})
            continue
        now = _stored_chunks_of(client, target_list, why["doc_id"], why["content_sha256"],
                                stored)
        before = set(why.get("indexed_before", []))
        record = {
            "file": why["file"], "reason": why["reason"],
            "in_quarantine": any((folder / name).is_file()
                                 for name in (why["file"], f"original-{why['file']}")),
            "indexed_chunks": len(now), "doc_id": why["doc_id"],
            "content_sha256": why["content_sha256"], "run_id": why["run_id"]}
        if now and run and run < why["run_id"] and set(now) <= before:
            apart.append({**record, "indexed_by_run": run, "indexed_from_file": source,
                          "chunks_in_store_before_this_delivery": len(before),
                          "chunks_added_since": len(set(now) - before)})
        else:
            documents.append(record)
    return {"collections": [t.collection for t in target_list],
            "counted_by": "chunks in these collections with the file's doc_id and "
                          "content_sha256",
            "documents": documents, "already_indexed": apart, "resolved": resolved}


def _stored_chunks_of(client: Any, target_list: list[Target], doc_id: str,
                      content_sha256: str, stored: dict | None = None) -> list[str]:
    """"collection:chunk id" of every chunk with this text under this doc_id."""
    if not content_sha256:
        return []
    found = []
    for target in target_list:
        chunks = (stored[target.collection] if stored is not None
                  else sync.chunks_in(client, target.collection, {"doc_id": doc_id}))
        found += [f"{target.collection}:{chunk_id}" for chunk_id, meta in chunks.items()
                  if meta.get("doc_id") == doc_id
                  and meta.get("content_sha256") == content_sha256]
    return sorted(found)


def _changed_proof(doc_id: str, before: dict, after: dict) -> list[str]:
    """What stops the before and after ids from showing "only this document changed"."""
    problems = []
    if doc_id not in before:
        problems.append(f"'{doc_id}' was not indexed before this run")
    elif before[doc_id] == after.get(doc_id):
        problems.append(f"the chunks of '{doc_id}' did not change in this run")
    others = sorted(d for d in set(before) | set(after)
                    if d != doc_id and before.get(d) != after.get(d))
    if others:
        problems.append(f"other documents changed too: {others}")
    return problems


# ---------------------------------------------------------------- the run
def run_ingest(settings: IndexerSettings, services: Services,
               sources: list[Path] | None = None, batch: str | None = None,
               prove_changed: str | None = None, adopt: bool = False) -> IngestResult:
    target_list = targets(settings)             # a bad target list fails before any work
    lake = Lake(Path(settings.lake_dir))
    reports = Reports(settings.reports_path)
    with IngestLock(lake.lock_path, settings.lock_stale_seconds) as lock:
        open_lake(lake)
        run_id, started = new_run_id(), utc_now()

        inputs = find_inputs(settings, lake, sources, batch)
        candidates = [load_candidate(lake, item) for item in inputs]
        windows = WindowVectors(
            lake.verify_cache_dir,
            embed=lambda texts: services.embed(texts, settings.verify_embed_model),
            model_key=lambda: (f"{settings.verify_embed_model}@"
                               f"{services.model_digest(settings.verify_embed_model)}"))
        verdicts = verify_batch(candidates, known_versions(lake), lake.pinned(), windows,
                                lake.manifest["retired"])
        # Read before this run writes anything (see build_quarantine_proof).
        indexed_before = {
            id(v): _stored_chunks_of(services.client, target_list, v.candidate.meta.doc_id,
                                     v.candidate.content_sha256)
            for v in verdicts if v.decision == "quarantined"}

        lock.check()
        not_copied: set[Path] = set()
        for verdict in verdicts:
            if verdict.decision == "accepted":
                lake.accept(verdict, run_id)
            elif verdict.decision == "quarantined":
                if not lake.quarantine(verdict, run_id, indexed_before[id(verdict)]):
                    not_copied.add(verdict.candidate.path)
        lake.save()
        lake.sync_folders()

        verification = {
            "run_id": run_id, "started_at": started,
            "batch": ",".join(sorted({item.batch for item in inputs})),
            "verify_embed_model": settings.verify_embed_model,
            "verification_embedding_calls": windows.calls,
            "documents": [v.entry() for v in verdicts],
        }
        reports.verification(verification)
        # Last: a run that dies before this point sees the files again.
        for item in inputs:
            if item.consume and item.path not in not_copied:
                item.path.unlink(missing_ok=True)
                sidecar_path(item.path).unlink(missing_ok=True)
        result = IngestResult(run_id, verification)
        if lake.has_never_held_a_document():
            raise EmptyLake(result)

        quarantined = sum(1 for v in verdicts if v.decision == "quarantined")
        versions = lake_versions(lake)
        for number, target in enumerate(target_list):
            proving = prove_changed if number == 0 else None
            before = (sync.ids_by_document(services.client, target.collection)
                      if proving else {})
            target_started = utc_now()
            counts = sync.reconcile(services.client, target, versions, lake.lake_id,
                                    lake.state_id(), services.embed,
                                    services.model_digest, lock.check, adopt=adopt)
            counts["documents_quarantined"] = quarantined
            counts["verification_embedding_calls"] = windows.calls   # per run, not per target
            report = {
                "run_id": run_id, "started_at": target_started,
                "collection": target.collection, "lake_state_id": lake.state_id(),
                "batch": verification["batch"], "embed_model": target.embed_model,
                "chunk_size": target.chunk_size, "chunk_overlap": target.chunk_overlap,
                "counts": counts}
            reports.ingest(report)
            result.ingest.append(report)
            if proving:
                after = sync.ids_by_document(services.client, target.collection)
                result.problems = _changed_proof(proving, before, after)
                # Written as read, even if it does not show what was hoped for.
                reports.proof("changed", {
                    "run_id": run_id, "started_at": started, "changed_doc_id": proving,
                    "collection": target.collection, "before": before, "after": after})

        if quarantined:
            reports.proof("quarantine", {
                "run_id": run_id, "started_at": started,
                **build_quarantine_proof(lake, services.client, target_list)})
    return result
