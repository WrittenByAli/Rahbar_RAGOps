"""Near-duplicate detection (D-06): documents are compared window by window.

One vector for a 60-page document would average away the differences that matter. So a
document is cut into overlapping windows of words, each window is embedded with one fixed
model, and two documents are compared by the share of windows that have a close match.

Window vectors are cached by the hash of the document's text, so a document is embedded
once, and a run that brings nothing new calls no embedding model.
"""

from __future__ import annotations

import hashlib
import json
import math
import operator
import os
from collections.abc import Callable
from pathlib import Path

from .thresholds import WINDOW_STRIDE, WINDOW_WORDS

Vector = list[float]


def _plain_dot(a: Vector, b: Vector) -> float:
    return sum(map(operator.mul, a, b))


# math.sumprod is new in Python 3.12 and much faster; it gives the same number.
_dot: Callable[[Vector, Vector], float] = getattr(math, "sumprod", _plain_dot)


def split_windows(text: str, size: int = WINDOW_WORDS, stride: int = WINDOW_STRIDE) -> list[str]:
    """Windows of `size` words, one starting every `stride` words, up to the end."""
    words = text.split()
    windows = []
    for start in range(0, len(words), stride):
        windows.append(" ".join(words[start:start + size]))
        if start + size >= len(words):
            break
    return windows


def unit(vector: Vector) -> Vector:
    """Scaled to length 1, so that the dot product of two vectors is their cosine."""
    length = math.sqrt(_dot(vector, vector)) or 1.0
    return [value / length for value in vector]


def best_similarities(windows: list[Vector], others: list[Vector]) -> list[float]:
    """For each window, the cosine of its closest window in the other document."""
    return [max((_dot(window, other) for other in others), default=0.0) for window in windows]


def share_at_least(similarities: list[float], limit: float) -> float:
    if not similarities:
        return 0.0
    return sum(1 for value in similarities if value >= limit) / len(similarities)


class WindowVectors:
    """The window vectors of a document, embedded once and then read from the cache."""

    def __init__(self, folder: Path, embed: Callable[[list[str]], list[Vector]],
                 model_key: Callable[[], str]) -> None:
        self.folder = folder
        self._embed = embed
        self._model_key = model_key         # "model@digest"; asked only when needed
        self._known: dict[str, list[Vector]] = {}
        self._key: str | None = None
        self.calls = 0                      # calls made to the embedding model

    def of(self, content_sha256: str, text: str) -> list[Vector]:
        if content_sha256 in self._known:
            return self._known[content_sha256]
        if self._key is None:
            self._key = self._model_key()
        name = f"{self._key}|{WINDOW_WORDS}|{WINDOW_STRIDE}|{content_sha256}"
        path = self.folder / f"{hashlib.sha256(name.encode()).hexdigest()}.json"
        if path.is_file():
            vectors = json.loads(path.read_text(encoding="utf-8"))
        else:
            windows = split_windows(text)
            vectors = [unit(v) for v in self._embed(windows)]
            self.calls += 1
            if len(vectors) != len(windows):
                raise RuntimeError(f"embedding model returned {len(vectors)} vectors "
                                   f"for {len(windows)} windows")
            self.folder.mkdir(parents=True, exist_ok=True)
            part = path.with_suffix(".part")
            part.write_text(json.dumps(vectors), encoding="utf-8")
            os.replace(part, path)          # a killed run leaves no half-written cache
        self._known[content_sha256] = vectors
        return vectors
