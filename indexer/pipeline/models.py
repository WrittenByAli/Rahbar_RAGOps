"""The shapes passed between stages. Metadata fields are required (section 3.1)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Page:
    """Text of one page, as extraction produced it."""

    source: str              # file name, e.g. Academic_Rules_Handbook_2026.pdf
    page: int                # 1-based
    text: str
    ocr: bool = False        # True when this page had no text layer and was OCR'd


@dataclass
class Document:
    source: str
    doc_version: str         # e.g. "2026-06"; from corpus/sources.yaml
    superseded: bool         # True for an older version of a policy
    pages: list[Page] = field(default_factory=list)
    doc_id: str = ""         # same for every version, e.g. "ug-academic-rules"

    def __post_init__(self) -> None:
        if not self.doc_id:
            self.doc_id = self.source


@dataclass
class Chunk:
    id: str                  # changes with doc_id, version, section, position or text
    text: str
    doc_id: str
    source: str
    section: str             # the heading this chunk sits under, e.g. "5.4 Academic Warning"
    page_start: int          # first and last page the words in `text` are printed on
    page_end: int
    doc_version: str
    superseded: bool

    @property
    def page(self) -> int:
        """The one page a citation shows. The API reads this name."""
        return self.page_start

    def metadata(self) -> dict[str, str | int | bool]:
        """Chroma metadata values must be str, int, float or bool."""
        return {
            "doc_id": self.doc_id,
            "source": self.source,
            "section": self.section,
            "page": self.page,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "doc_version": self.doc_version,
            "superseded": self.superseded,
        }
