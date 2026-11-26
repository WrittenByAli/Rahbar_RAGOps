"""The data lake on disk (ARCHITECTURE 3.1). Everything here is on one volume.

    inbox/<batch>/              files waiting to be ingested, each with an optional sidecar
    lake/<doc_id>/<version>/    the active version: original, meta.json, extracted.json
    archive/<doc_id>/<version>/ superseded, replaced and deleted versions
    quarantine/<run_id>/<file>/ a rejected file and reason.json
    state/                      manifest.json, ingest.lock and two caches

The manifest is the desired state of retrieval: the vector store is made to match it.
A document leaves it only through an explicit delete. A file that is missing from the
inbox means nothing (D-05).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .clean import CLEANER_VERSION
from .extract import EXTRACTOR_VERSION
from .metadata import sidecar_path
from .models import Page
from .verify import Verdict

ACTIVE, SUPERSEDED = "active", "superseded"
Entry = dict[str, Any]


def write_json(path: Path, data: object) -> None:
    """All or nothing: a killed run never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.part")
    part.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(part, path)


class Lake:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.inbox = self.root / "inbox"
        self.active_dir = self.root / "lake"
        self.archive_dir = self.root / "archive"
        self.quarantine_dir = self.root / "quarantine"
        state = self.root / "state"
        self.manifest_path = state / "manifest.json"
        self.lock_path = state / "ingest.lock"
        self.extract_cache_dir = state / "extract-cache"
        self.verify_cache_dir = state / "verify-cache"
        self.manifest: dict[str, Any] = {}

    # ---------------------------------------------------------------- manifest
    def load(self) -> None:
        if self.manifest_path.is_file():
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        else:
            self.manifest = {"lake_id": uuid.uuid4().hex, "documents": {}, "deleted": []}
        # Files that were corrected or deleted on purpose (see verify._retired_reason).
        self.manifest.setdefault("retired", [])

    def save(self) -> None:
        write_json(self.manifest_path, self.manifest)

    @property
    def lake_id(self) -> str:
        return self.manifest["lake_id"]

    def versions(self) -> Iterator[tuple[str, str, Entry]]:
        """(doc_id, version, entry) for every version the vector store must hold."""
        for doc_id, document in sorted(self.manifest["documents"].items()):
            for version, entry in sorted(document["versions"].items()):
                yield doc_id, version, entry

    def has_never_held_a_document(self) -> bool:
        return not self.manifest["documents"] and not self.manifest["deleted"]

    def pinned(self) -> set[str]:
        return {doc_id for doc_id, d in self.manifest["documents"].items() if d["pinned"]}

    def state_id(self) -> str:
        """A hash of what can be retrieved: every active version and its text."""
        active = [[doc_id, version, entry["content_sha256"]]
                  for doc_id, version, entry in self.versions() if entry["status"] == ACTIVE]
        return hashlib.sha256(json.dumps(active).encode()).hexdigest()

    # ---------------------------------------------------------------- folders
    def folder(self, doc_id: str, version: str, status: str) -> Path:
        root = self.active_dir if status == ACTIVE else self.archive_dir
        return root / doc_id / version

    def sync_folders(self) -> None:
        """Put every version's folder where the manifest says: lake/ if active, archive/
        if not. Safe to repeat, so it also repairs what a killed run left behind: a move
        not done, a new folder the manifest never recorded, or a version already set
        aside for a correction that was never recorded."""
        for doc_id, version, entry in self.versions():
            wanted = self.folder(doc_id, version, entry["status"])
            if _holds(wanted, entry):
                continue
            other = self.folder(doc_id, version,
                                SUPERSEDED if entry["status"] == ACTIVE else ACTIVE)
            set_aside = sorted((self.archive_dir / doc_id).glob(f"{version}~*"))
            found = next((f for f in [other, *set_aside] if _holds(f, entry)), None)
            if found is not None:
                self._clear(wanted, doc_id, version)
                _move(found, wanted)

    def _clear(self, folder: Path, doc_id: str, version: str) -> None:
        """Move aside a folder the manifest does not own, so that its place is free."""
        if folder.exists():
            _move(folder, _unused(self.archive_dir / doc_id / f"{version}~orphaned"))

    def pages(self, doc_id: str, version: str) -> list[Page]:
        """The text as it was extracted. Cleaning happens again at each use, so a better
        cleaner reaches documents that are already in the lake."""
        status = self.manifest["documents"][doc_id]["versions"][version]["status"]
        path = self.folder(doc_id, version, status) / "extracted.json"
        return [Page(**page) for page in json.loads(path.read_text(encoding="utf-8"))]

    def replace_pages(self, doc_id: str, version: str, pages: list[Page]) -> None:
        """Store the text of a version as extracted again, by a newer extractor."""
        entry = self.manifest["documents"][doc_id]["versions"][version]
        write_json(self.folder(doc_id, version, entry["status"]) / "extracted.json",
                   [asdict(page) for page in pages])
        entry["extractor_version"] = EXTRACTOR_VERSION
        entry.pop("cleaner_version", None)          # so content_sha256 is taken again

    def _set_aside(self, doc_id: str, version: str, status: str, label: str) -> None:
        """Keep a copy that is no longer a version. "~" cannot occur in a version."""
        current = self.folder(doc_id, version, status)
        if current.exists():
            _move(current, _unused(self.archive_dir / doc_id / f"{version}~{label}"))

    # ---------------------------------------------------------------- changes
    def accept(self, verdict: Verdict, run_id: str) -> None:
        c, action = verdict.candidate, verdict.action
        doc_id, version = c.meta.doc_id, c.meta.doc_version
        document = self.manifest["documents"].setdefault(
            doc_id, {"pinned": None, "versions": {}})
        previous = document["versions"].get(version)
        if action == "newer":
            for entry in document["versions"].values():
                entry["status"] = SUPERSEDED
            document["pinned"] = None           # a version never seen before ends a restore
        status = SUPERSEDED if action == "changed_archived" else ACTIVE
        entry = {
            "status": status, "file": c.file, "title": c.meta.title, "source": c.meta.source,
            "doc_version": version, "page_count": len(c.pages),
            "metadata_source": c.meta.sources, "raw_sha256": c.raw_sha256,
            "content_sha256": c.content_sha256, "cleaner_version": CLEANER_VERSION,
            "extractor_version": EXTRACTOR_VERSION,
            "batch": c.batch, "run_id": run_id,
        }

        staging = self.root / "state" / "staging" / uuid.uuid4().hex
        staging.mkdir(parents=True)
        shutil.copyfile(c.path, staging / c.file)
        write_json(staging / "extracted.json", [asdict(page) for page in c.pages])
        write_json(staging / "meta.json", {"doc_id": doc_id, **entry})
        if previous:                            # corrected copy: keep the one it replaces
            self._set_aside(doc_id, version, previous["status"], f"replaced-{run_id}")
            if previous["raw_sha256"] != c.raw_sha256:
                self._retire(doc_id, version, previous, "replaced", run_id)
        target = self.folder(doc_id, version, status)
        self._clear(target, doc_id, version)    # left by a run that died before saving
        _move(staging, target)
        document["versions"][version] = entry

    def _retire(self, doc_id: str, version: str, entry: Entry, why: str,
                run_id: str) -> None:
        self.manifest["retired"].append({
            "doc_id": doc_id, "version": version, "why": why, "run_id": run_id,
            "raw_sha256": entry["raw_sha256"], "content_sha256": entry["content_sha256"]})

    def quarantine(self, verdict: Verdict, run_id: str,
                   indexed_before: list[str] | None = None) -> bool:
        """Copy a rejected file to quarantine with its reason. False if the file itself
        could not be copied; it must then stay where it is."""
        c = verdict.candidate
        folder = _unused(self.quarantine_dir / run_id / c.file)    # one name, two batches
        folder.mkdir(parents=True)
        kept = folder / (f"original-{c.file}" if c.file == "reason.json" else c.file)
        copied = True
        try:
            shutil.copyfile(c.path, kept)
            if sidecar_path(c.path).is_file():
                shutil.copyfile(sidecar_path(c.path), sidecar_path(kept))
        except OSError:
            copied = False
        failed = {name: verdict.checks[name].reason for name in verdict.failed_checks}
        write_json(folder / "reason.json", {
            "run_id": run_id, "file": c.file, "batch": c.batch, "doc_id": c.meta.doc_id,
            "doc_version": c.meta.doc_version, "failed_checks": list(failed),
            "reason": "; ".join(f"{name}: {why}" for name, why in failed.items()),
            "checks": verdict.entry()["checks"], "raw_sha256": c.raw_sha256,
            "content_sha256": c.content_sha256,
            # "collection:chunk id" of this text already stored when the file arrived.
            "indexed_before": indexed_before or [],
        })
        return copied

    def quarantined(self) -> list[tuple[Path, Entry]]:
        """(folder, reason.json) of every quarantined file, oldest run first."""
        return [(path.parent, json.loads(path.read_text(encoding="utf-8")))
                for path in sorted(self.quarantine_dir.glob("*/*/reason.json"))]

    def restore(self, doc_id: str, version: str) -> str:
        """Make an archived version the active one. Returns the version it replaces.

        The choice is recorded ("pinned"), so that delivering the newer version again
        does not promote it."""
        document = self.manifest["documents"].get(doc_id)
        if document is None:
            raise LookupError(f"no document '{doc_id}' in the data lake")
        if version not in document["versions"]:
            raise LookupError(f"'{doc_id}' has no version '{version}'; it has: "
                              f"{', '.join(sorted(document['versions']))}")
        if document["versions"][version]["status"] == ACTIVE:
            raise LookupError(f"version '{version}' of '{doc_id}' is already active")
        [replaced] = [v for v, e in document["versions"].items() if e["status"] == ACTIVE]
        document["versions"][replaced]["status"] = SUPERSEDED
        document["versions"][version]["status"] = ACTIVE
        document["pinned"] = version
        return replaced

    def delete(self, doc_id: str, run_id: str) -> None:
        """Remove every version of a document. The files are kept in the archive."""
        document = self.manifest["documents"].pop(doc_id)
        for version, entry in document["versions"].items():
            self._set_aside(doc_id, version, entry["status"], f"deleted-{run_id}")
            self._retire(doc_id, version, entry, "deleted", run_id)
        self.manifest["deleted"].append(
            {"doc_id": doc_id, "versions": sorted(document["versions"]), "run_id": run_id})

    # ---------------------------------------------------------------- extraction cache
    def _extraction_path(self, raw_sha256: str, suffix: str) -> Path:
        return self.extract_cache_dir / f"{raw_sha256}{suffix}.x{EXTRACTOR_VERSION}.json"

    def cached_extraction(self, raw_sha256: str, suffix: str) -> list[Page] | None:
        path = self._extraction_path(raw_sha256, suffix)
        if not path.is_file():
            return None
        return [Page(**page) for page in json.loads(path.read_text(encoding="utf-8"))]

    def cache_extraction(self, raw_sha256: str, suffix: str, pages: list[Page]) -> None:
        """So that a file is parsed, and above all OCR'd, only once."""
        write_json(self._extraction_path(raw_sha256, suffix), [asdict(p) for p in pages])

    def refresh_content_hashes(self, version_hash: Callable[[str, str], str],
                               cleaner_version: str) -> None:
        """content_sha256 is taken from cleaned text. After the cleaner changes, take it
        again, so that an unchanged file is still recognised as unchanged."""
        for doc_id, version, entry in self.versions():
            if entry.get("cleaner_version") != cleaner_version:
                entry["content_sha256"] = version_hash(doc_id, version)
                entry["cleaner_version"] = cleaner_version


def _holds(folder: Path, entry: Entry) -> bool:
    """Whether a version folder is the one this manifest entry describes."""
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (meta.get("run_id") == entry["run_id"]
            and meta.get("raw_sha256") == entry["raw_sha256"])


def _unused(path: Path) -> Path:
    """The path itself, or with a number added, so that nothing is ever overwritten."""
    candidate, number = path, 2
    while candidate.exists():
        candidate, number = path.with_name(f"{path.name}-{number}"), number + 1
    return candidate


def _move(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.rename(source, target)
