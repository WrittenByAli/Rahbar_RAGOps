"""Indexer configuration, from the same ConfigMap the API reads (section 4.1)."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml  # type: ignore[import-untyped]


def _temporary_lake() -> str:
    """Where the lake goes when LAKE_DIR is not set, as in CI. It does not survive the
    pod: a cluster that keeps a data lake must set LAKE_DIR to its volume."""
    return str(Path(tempfile.gettempdir()) / "rahbar-lake")


@dataclass(frozen=True)
class IndexerSettings:
    embed_model: str        # EMBED_MODEL: must equal the API's value (section 4.4)
    chroma_host: str
    chroma_port: int
    collection: str
    corpus_dir: str         # optional folder of documents that is only read
    chunk_size: int
    chunk_overlap: int
    ollama_url: str
    lake_dir: str = field(default_factory=_temporary_lake)
    report_dir: str = ""                        # "" means <lake_dir>/reports
    verify_embed_model: str = "nomic-embed-text"    # does not change with the release
    index_targets: str = ""                     # YAML list; "" means the one collection
    lock_stale_seconds: int = 300

    @property
    def reports_path(self) -> Path:
        return Path(self.report_dir) if self.report_dir else Path(self.lake_dir) / "reports"


@dataclass(frozen=True)
class Target:
    """A collection that ingestion keeps up to date, with the settings that shape it."""

    collection: str
    embed_model: str
    chunk_size: int
    chunk_overlap: int


def targets(settings: IndexerSettings) -> list[Target]:
    """INDEX_TARGETS lists every collection to maintain: live, previous and candidate. A
    collection left out goes stale, and a rollback to it would serve old documents."""
    if not settings.index_targets.strip():
        return [Target(settings.collection, settings.embed_model, settings.chunk_size,
                       settings.chunk_overlap)]
    entries = yaml.safe_load(settings.index_targets)
    keys = ("collection", "embed_model", "chunk_size", "chunk_overlap")
    # Every field is required: a candidate built with a default by mistake would be tested
    # as something it is not.
    if not isinstance(entries, list) or not entries or not all(
            isinstance(e, dict) and set(e) == set(keys) for e in entries):
        raise ValueError(f"INDEX_TARGETS must be a list of entries with exactly {keys}")
    found = [Target(str(e["collection"]), str(e["embed_model"]), int(e["chunk_size"]),
                    int(e["chunk_overlap"])) for e in entries]
    names = [t.collection for t in found]
    if len(set(names)) != len(names):
        raise ValueError(f"INDEX_TARGETS names a collection twice: {names}")
    return found


def get_settings() -> IndexerSettings:
    return IndexerSettings(
        embed_model=os.environ.get("EMBED_MODEL", "nomic-embed-text"),
        chroma_host=os.environ.get("CHROMA_HOST", "chroma"),
        chroma_port=int(os.environ.get("CHROMA_PORT", "8000")),
        collection=os.environ.get("CHROMA_COLLECTION", "rahbar"),
        corpus_dir=os.environ.get("CORPUS_DIR", "/corpus"),
        chunk_size=int(os.environ.get("CHUNK_SIZE", "400")),
        chunk_overlap=int(os.environ.get("CHUNK_OVERLAP", "50")),
        ollama_url=os.environ.get("OLLAMA_URL", "http://host.minikube.internal:11434"),
        lake_dir=os.environ.get("LAKE_DIR") or _temporary_lake(),
        report_dir=os.environ.get("REPORT_DIR", ""),
        verify_embed_model=os.environ.get("VERIFY_EMBED_MODEL") or "nomic-embed-text",
        index_targets=os.environ.get("INDEX_TARGETS", ""),
        lock_stale_seconds=int(os.environ.get("LOCK_STALE_SECONDS", "300")),
    )
