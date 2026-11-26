"""Document metadata and where each field came from (D-04, ARCHITECTURE 3.3).

Each field is taken from the first place that has it:

1. the sidecar "<file name>.meta.yaml"
2. Markdown front matter (the same keys between two "---" lines)
3. metadata embedded in the file: HTML <title>, or the PDF document information

Nothing is guessed. A title, source or date that none of these gives stays empty, and
the completeness check then fails. Only doc_id may be derived, and it is marked so.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

SIDECAR_SUFFIX = ".meta.yaml"
TEXT_FIELDS = ("doc_id", "title", "source", "doc_version")

# A doc_id is used as a folder name in the data lake.
_DOC_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}")
_VERSION = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")
# Titles that authoring tools write by themselves. They name nothing.
_PLACEHOLDER_TITLES = {"", "untitled", "none", "document", "title"}
_FILE_NAME = re.compile(r"\.(docx?|pdf|pptx?|xlsx?|cdr|indd|html?|txt|md)$")


@dataclass
class Metadata:
    doc_id: str = ""
    title: str = ""
    source: str = ""                 # where the document came from, for example a URL
    doc_version: str = ""
    declared_page_count: int | None = None
    declared_size_bytes: int | None = None
    declared_sha256: str = ""
    sources: dict[str, str] = field(default_factory=dict)    # field -> where it was found
    problems: list[str] = field(default_factory=list)        # unusable metadata


def sidecar_path(document: Path) -> Path:
    return document.with_name(document.name + SIDECAR_SUFFIX)


def valid_text(text: str) -> str:
    """Without lone surrogates. YAML escapes and damaged PDFs can produce them, and a
    string that holds one cannot be written to any UTF-8 file."""
    return text.encode("utf-8", "replace").decode("utf-8")


def front_matter(text: str, problems: list[str] | None = None) -> tuple[dict[str, Any], str]:
    """Split Markdown into (front matter, body). Without a closed block of "key: value"
    lines at the top, the whole text is the body. A block that cannot be parsed is also
    left in the body, and reported in `problems`."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    for end in range(1, len(lines)):
        if lines[end].strip() == "---":
            try:
                block = yaml.safe_load("".join(lines[1:end]))
            # Not only YAMLError: "2026-02-30" raises ValueError, deep nesting
            # RecursionError.
            except Exception as exc:  # noqa: BLE001
                if problems is not None:
                    problems.append(f"front matter is not valid YAML ({type(exc).__name__}: "
                                    f"{str(exc)[:80]})")
                return {}, text
            if not isinstance(block, dict):
                return {}, text
            return block, "".join(lines[end + 1:])
    return {}, text


def resolve(path: Path, raw_sha256: str) -> Metadata:
    """Metadata of one document file. `raw_sha256` is the hash of its bytes."""
    meta = Metadata()
    layers: list[tuple[str, dict[str, Any]]] = []
    if sidecar_path(path).is_file():
        layers.append(("sidecar", _read_sidecar(sidecar_path(path), meta.problems)))
    if path.suffix.lower() == ".md":
        layers.append(("front_matter", front_matter(_read_text(path), meta.problems)[0]))
    layers.append(_embedded(path))

    for name in TEXT_FIELDS:
        for origin, values in layers:
            value = _as_text(values.get(name), name, origin, meta.problems)
            if value:
                setattr(meta, name, value)
                meta.sources[name] = origin
                break
    _read_declared(meta, [values for origin, values in layers
                          if origin in ("sidecar", "front_matter")])

    if meta.doc_id and not _DOC_ID.fullmatch(meta.doc_id):
        meta.problems.append(f"doc_id '{meta.doc_id}' must use only lower-case letters, "
                             "digits, dot, dash and underscore")
        meta.doc_id = ""
    if not meta.doc_id:
        meta.doc_id = derive_doc_id(path.name, raw_sha256)
        meta.sources["doc_id"] = "derived"
    meta.declared_sha256 = valid_text(meta.declared_sha256)
    meta.problems = [valid_text(problem) for problem in meta.problems]
    return meta


def derive_doc_id(file_name: str, raw_sha256: str) -> str:
    """An identity for a document that states none: its file name and a hash of its bytes.

    The same bytes under the same name always give the same id. Different bytes give a
    different one, so two unrelated files that share a name are never treated as two
    versions of one document. Joining a family needs a sidecar (ARCHITECTURE 3.3)."""
    slug = re.sub(r"[^a-z0-9]+", "-", Path(file_name).stem.lower()).strip("-")[:60]
    return f"{slug or 'document'}-{raw_sha256[:8]}"


def version_key(version: str) -> tuple[int, int, int] | None:
    """A sortable key for YYYY, YYYY-MM, YYYY-MM-DD or an academic year YYYY-YY.
    None for anything else. "2019-20" is an academic year, because there is no month 20;
    "2023-08" is a month."""
    match = _VERSION.fullmatch(version)
    if not match:
        return None
    year, second, day = int(match[1]), match[2], match[3]
    if second is None:
        return (year, 0, 0)
    if day is not None:
        try:
            date(year, int(second), int(day))
        except ValueError:
            return None
        return (year, int(second), int(day))
    if 1 <= int(second) <= 12:
        return (year, int(second), 0)
    if int(second) == (year + 1) % 100:
        return (year, 0, 0)
    return None


def version_order(version: str) -> tuple[tuple[int, int, int], str]:
    """Sort key for any version string; unusable ones come first."""
    return (version_key(version) or (0, 0, 0), version)


def normalise_title(title: str) -> str:
    """Lower case without digits and punctuation, so that years do not count."""
    return " ".join(re.sub(r"[\W\d_]+", " ", title.lower()).split())


# ---------------------------------------------------------------- the three layers
def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _read_sidecar(path: Path, problems: list[str]) -> dict[str, Any]:
    try:
        values = yaml.safe_load(_read_text(path))
    except Exception as exc:  # noqa: BLE001 - any parser failure concerns this file only
        problems.append(f"sidecar is not valid YAML ({type(exc).__name__}: {str(exc)[:80]})")
        return {}
    if not isinstance(values, dict):
        problems.append("sidecar must hold 'key: value' lines")
        return {}
    return values


def _embedded(path: Path) -> tuple[str, dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf_info", _pdf_info(path)
    if suffix in (".html", ".htm"):
        found = re.search(r"<title[^>]*>(.*?)</title>", _read_text(path), re.I | re.S)
        title = " ".join(unescape(found[1]).split()) if found else ""
        return "html_title", {"title": _real_title(title)}
    return "none", {}


def _pdf_info(path: Path) -> dict[str, str]:
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.CRITICAL)
    try:
        info: Any = PdfReader(str(path)).metadata or {}
        title = str(info.get("/Title") or "")
        created = str(info.get("/CreationDate") or "")
    except Exception:  # noqa: BLE001 - a damaged file has no metadata; quality reports it
        return {}
    values = {"title": _real_title(title)}
    stamp = re.match(r"D:(\d{4})(\d{2})(\d{2})", created)
    if stamp and version_key(f"{stamp[1]}-{stamp[2]}-{stamp[3]}"):
        values["doc_version"] = f"{stamp[1]}-{stamp[2]}-{stamp[3]}"
    return values


def _real_title(title: str) -> str:
    """Empty for a placeholder or a file name that a tool saved as the title."""
    plain = title.strip().lower()
    if (plain in _PLACEHOLDER_TITLES or plain.startswith("microsoft word - ")
            or _FILE_NAME.search(plain)):
        return ""
    return title.strip()


def _as_text(value: Any, name: str, origin: str, problems: list[str]) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        problems.append(f"{origin} field '{name}' must be a single value")
        return ""
    text = str(value).strip()           # YAML reads 2026 as a number, 2026-06-15 as a date
    if valid_text(text) != text:
        problems.append(f"{origin} field '{name}' holds characters that are not valid text")
        return ""
    return text


def _read_declared(meta: Metadata, layers: list[dict[str, Any]]) -> None:
    """What the sidecar or front matter claims about the file, to detect truncation."""
    for values in layers:
        for key, attribute in (("page_count", "declared_page_count"),
                               ("size_bytes", "declared_size_bytes")):
            if values.get(key) is not None and getattr(meta, attribute) is None:
                try:
                    setattr(meta, attribute, int(values[key]))
                except (TypeError, ValueError, OverflowError):      # ".inf" overflows
                    meta.problems.append(f"declared {key} '{values[key]}' is not a number")
        if values.get("sha256") and not meta.declared_sha256:
            meta.declared_sha256 = str(values["sha256"]).strip().lower()
