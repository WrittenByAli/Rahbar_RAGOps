"""Test doubles and file builders shared by the Phase 2 tests. No network, no server.

FakeCollection behaves like Chroma 1.0.12 on the points listed in ARCHITECTURE 3.6:
`update` merges metadata, `modify` replaces the whole collection metadata and raises when
it holds "hnsw:space", `get` and `delete` accept `where` (also with "$and"), and an empty
list of ids is an error.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from pathlib import Path
from typing import Any

from pipeline.config import IndexerSettings
from pipeline.ingest import Services

DIM = 256


class FakeCollection:
    def __init__(self, name: str, metadata: dict | None = None) -> None:
        self.name = name
        self.metadata = dict(metadata or {})
        self.rows: dict[str, dict] = {}
        self.writes: list[str] = []         # every call that changed something
        self.generations: list[str] = []    # each index_generation written by modify()

    # ---- reads
    def count(self) -> int:
        return len(self.rows)

    def get(self, ids: list[str] | None = None, where: dict | None = None,
            include: list[str] | None = None) -> dict:
        chosen = [i for i, row in self.rows.items()
                  if (ids is None or i in ids) and _matches(row["metadata"], where)]
        result: dict[str, Any] = {"ids": chosen}
        for key, field in (("metadatas", "metadata"), ("documents", "document"),
                           ("embeddings", "embedding")):
            if include is None or key in include:
                result[key] = [_copy(self.rows[i][field]) for i in chosen]
        return result

    # ---- writes
    def upsert(self, ids: list[str], embeddings: list, documents: list[str],
               metadatas: list[dict]) -> None:
        _check_batch(ids, embeddings, documents, metadatas)
        for i, vector, text, meta in zip(ids, embeddings, documents, metadatas):
            _check_metadata(meta)
            self.rows[i] = {"embedding": list(vector), "document": text, "metadata": dict(meta)}
        self.writes.append(f"upsert {len(ids)}")

    add = upsert

    def update(self, ids: list[str], metadatas: list[dict]) -> None:
        _check_batch(ids, metadatas)
        for i, meta in zip(ids, metadatas):
            _check_metadata(meta)
            self.rows[i]["metadata"].update(meta)       # merges, like the real server
        self.writes.append(f"update {len(ids)}")

    def delete(self, ids: list[str] | None = None, where: dict | None = None) -> None:
        if ids is not None and not ids:
            raise ValueError("Expected IDs to be a non-empty list")
        if ids is None and not where:
            raise ValueError("delete needs ids or where")
        doomed = [i for i, row in self.rows.items()
                  if (ids is None or i in ids) and _matches(row["metadata"], where)]
        for i in doomed:
            del self.rows[i]
        self.writes.append(f"delete {len(doomed)}")

    def modify(self, metadata: dict) -> None:
        if "hnsw:space" in metadata:
            raise ValueError("Changing the distance function of a collection is not supported")
        _check_metadata(metadata)
        self.metadata = dict(metadata)                  # replaces, like the real server
        if "index_generation" in metadata:
            self.generations.append(metadata["index_generation"])
        self.writes.append("modify")


class FakeClient:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self.collections:
            raise ValueError(f"Collection {name} does not exist.")
        return self.collections[name]

    def create_collection(self, name: str, metadata: dict | None = None) -> FakeCollection:
        if name in self.collections:
            raise ValueError(f"Collection {name} already exists.")
        self.collections[name] = FakeCollection(name, metadata)
        return self.collections[name]

    def delete_collection(self, name: str) -> None:
        if name not in self.collections:
            raise ValueError(f"Collection {name} does not exist.")
        del self.collections[name]

    def list_collections(self) -> list[FakeCollection]:
        return list(self.collections.values())


def _copy(value: Any) -> Any:
    return dict(value) if isinstance(value, dict) else value


def _matches(metadata: dict, where: dict | None) -> bool:
    if not where:
        return True
    if "$and" in where:
        return all(_matches(metadata, part) for part in where["$and"])
    if len(where) != 1:
        raise ValueError("Expected where to have exactly one operator")
    [(key, wanted)] = where.items()
    if isinstance(wanted, dict):
        wanted = wanted["$eq"]
    return key in metadata and metadata[key] == wanted


def _check_batch(*columns: list) -> None:
    if not columns[0]:
        raise ValueError("Expected IDs to be a non-empty list")
    if len({len(column) for column in columns}) != 1:
        raise ValueError("columns differ in length")
    if len(set(columns[0])) != len(columns[0]):
        raise ValueError("Expected IDs to be unique")


def _check_metadata(metadata: dict) -> None:
    for key, value in metadata.items():
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"metadata value for {key!r} must be str, int, float or bool")


class FakeEmbedder:
    """A bag-of-words vector: texts that share most words score close to 1.0, unrelated
    texts close to 0. It records every call, so tests can prove nothing was embedded."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, texts: list[str], model: str) -> list[list[float]]:
        self.calls.append((model, list(texts)))
        return [_bag_of_words(text) for text in texts]

    @property
    def texts_embedded(self) -> int:
        return sum(len(texts) for _, texts in self.calls)

    def reset(self) -> None:
        self.calls.clear()


def _bag_of_words(text: str) -> list[float]:
    values = [0.0] * DIM
    for word in text.lower().split():
        digest = hashlib.sha256(word.encode()).digest()
        values[int.from_bytes(digest[:4], "big") % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def services(client: FakeClient | None = None,
             embedder: FakeEmbedder | None = None) -> Services:
    return Services(client=client or FakeClient(), embed=embedder or FakeEmbedder(),
                    model_digest=lambda model: f"digest-of-{model}")


def settings(tmp_path: Path, **changes: Any) -> IndexerSettings:
    values: dict[str, Any] = dict(
        embed_model="nomic-embed-text", chroma_host="", chroma_port=0, collection="rahbar",
        corpus_dir=str(tmp_path / "no-corpus"), chunk_size=40, chunk_overlap=5,
        ollama_url="", lake_dir=str(tmp_path / "lake"), report_dir=str(tmp_path / "reports"))
    values.update(changes)
    return IndexerSettings(**values)


_SYLLABLES = ["ba", "co", "du", "fe", "gi", "ha", "jo", "ku", "le", "mi", "no", "pu",
              "ra", "si", "to", "vu", "wa", "ye", "zo", "qui"]


def prose(seed: int, words: int = 300) -> str:
    """Pronounceable nonsense. Different seeds share almost no words."""
    rng = random.Random(seed)
    out = ["".join(rng.choice(_SYLLABLES) for _ in range(rng.randint(3, 5)))
           for _ in range(words)]
    return "\n\n".join(" ".join(out[i:i + 40]) for i in range(0, len(out), 40))


def edit_words(text: str, every: int) -> str:
    """Replace every n-th word: a copy with small wording edits."""
    count = 0

    def swap(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "reworded" if count % every == 0 else match.group(0)

    return re.sub(r"\S+", swap, text)


def put_markdown(lake_dir: str | Path, name: str, body: str, batch: str = "A",
                 **meta: object) -> Path:
    """Put a Markdown file with front matter into the lake inbox. A field passed as None
    is left out of the front matter."""
    fields: dict[str, object] = {
        "doc_id": Path(name).stem, "title": f"Title of {Path(name).stem}",
        "source": f"https://example.test/{name}", "doc_version": "2026-01"}
    fields.update(meta)
    front = "".join(f'{key}: "{value}"\n' for key, value in fields.items() if value is not None)
    folder = Path(lake_dir) / "inbox" / batch
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(f"---\n{front}---\n{body}\n", encoding="utf-8")
    return path


def make_pdf(path: Path, pages: list[list[tuple[int, int, str]]], width: int = 400,
             height: int = 600, info: dict[str, str] | None = None,
             media_box: tuple[int, int, int, int] | None = None,
             crop_box: tuple[int, int, int, int] | None = None) -> Path:
    """Write a small but well-formed PDF. Each page is a list of (x, y, text) lines.
    media_box and crop_box (x0, y0, x1, y1 in PDF units) apply to every page."""
    boxes = "/MediaBox [%d %d %d %d]" % (media_box or (0, 0, width, height))
    if crop_box:
        boxes += " /CropBox [%d %d %d %d]" % crop_box
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b"",
                            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        stream = "".join(f"BT /F1 9 Tf {x} {y} Td ({text}) Tj ET\n"
                         for x, y, text in lines).encode("latin-1")
        content_number = len(objects) + 2
        objects.append(
            f"<< /Type /Page /Parent 2 0 R {boxes} "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_number} 0 R >>".encode())
        kids.append(f"{len(objects)} 0 R")
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()

    trailer_extra = ""
    if info:
        entries = " ".join(f"/{key} ({value})" for key, value in info.items())
        objects.append(f"<< {entries} >>".encode())
        trailer_extra = f" /Info {len(objects)} 0 R"

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R{trailer_extra} >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path
