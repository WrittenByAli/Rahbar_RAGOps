"""Small helpers shared by the suite, the gate and the other evaluation scripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

EVAL_DIR = Path(__file__).resolve().parents[1]

# The nine component and end-to-end metrics (docs/a3/CONTRACTS.md).
SUITE_METRICS = (
    "component.recall_at_3",
    "component.recall_at_5",
    "component.mrr",
    "component.ndcg_at_5",
    "end_to_end.faithfulness",
    "end_to_end.abstention_correctness",
    "end_to_end.citation_validity",
    "end_to_end.latency_ms_p50",
    "end_to_end.latency_ms_p95",
)
MODULE_METRICS = (
    "module.paraphrase_mean",
    "module.unrelated_mean",
    "module.gap",
    "module.embed_ms_per_chunk",
    "module.determinism_max_abs_diff",
)
# Two runs can be compared only if they were judged the same way.
JUDGE_SETTINGS = ("seed", "config.judge.model", "config.judge.digest",
                  "config.judge.prompt_sha256", "config.judge.seed")


class SuiteError(Exception):
    """A run or a comparison that cannot be trusted. The scripts exit with code 2."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Hash of a text file with CRLF read as LF, so that a Windows checkout and the Linux
    image give the same hash for the same file."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise SuiteError(f"cannot read {path}: {exc}") from exc
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def load_jsonl(path: str | Path, required: tuple[str, ...] = ()) -> list[dict]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, ValueError) as exc:
        raise SuiteError(f"cannot read {path}: {exc}") from exc
    rows = []
    # Split on the newline only: str.splitlines() also splits on U+2028 inside a string.
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise SuiteError(f"{path}:{number}: not valid JSON ({exc})") from exc
        if not isinstance(row, dict) or any(row.get(key) in (None, "") for key in required):
            raise SuiteError(f"{path}:{number}: needs the keys {', '.join(required)}")
        rows.append(row)
    if not rows:
        raise SuiteError(f"{path} has no rows")
    return rows


def load_json(path: str | Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise SuiteError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SuiteError(f"{path} is not a JSON object")
    return data


def get(data: Any, dotted: str) -> Any:
    """data["a"]["b"] for "a.b", or None when any part is missing."""
    for part in dotted.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


def require_same(first: dict, second: dict, keys: tuple[str, ...]) -> None:
    """SuiteError unless both results have each setting and the two are equal. A setting
    missing on both sides is not "the same"."""
    for key in keys:
        a, b = get(first, key), get(second, key)
        if a in (None, "") or a != b:
            raise SuiteError(f"'{key}' differs or is missing: {a!r} and {b!r}")


def utc_now() -> datetime:
    return datetime.now(UTC)


def stamp(when: datetime) -> str:
    return when.strftime("%Y%m%dT%H%M%SZ")


def label_part(label: str) -> str:
    """A label that is safe inside a file name."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-.") or "run"


def write_new_json(folder: str | Path, name: str, data: dict) -> Path:
    """Write <folder>/<name>.json. An existing result is never overwritten."""
    path = Path(folder) / f"{name}.json"
    try:
        text = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except (OSError, ValueError) as exc:
        raise SuiteError(f"cannot write {path}: {exc}") from exc
    return path


def git_sha() -> str:
    """The commit that is being run: GIT_SHA (set in the image), else asked from git."""
    sha = os.environ.get("GIT_SHA", "").strip()
    if not sha:
        try:
            run = subprocess.run(["git", "-C", str(EVAL_DIR), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, timeout=30, check=False)
            sha = run.stdout.strip() if run.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            sha = ""
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        raise SuiteError("no commit id: set GIT_SHA, or run inside the git repository")
    return sha


def post_json(client: httpx.Client, url: str, body: dict) -> dict:
    """POST and return the JSON object. httpx.HTTPError or ValueError on any failure."""
    response = client.post(url, json=body)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise TypeError(f"{url} did not return a JSON object")
    return data


def embed(client: httpx.Client, ollama_url: str, model: str, texts: list[str],
          keep_alive: int | None = None) -> list[list[float]]:
    """One vector per text, from Ollama. The text is sent as it is, as the pipeline does."""
    body: dict[str, Any] = {"model": model, "input": texts}
    if keep_alive is not None:
        body["keep_alive"] = keep_alive     # 0 unloads the model after this request
    try:
        data = post_json(client, f"{ollama_url.rstrip('/')}/api/embed", body)
        vectors = data["embeddings"]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise SuiteError(f"embedding with {model} failed: {exc}") from exc
    if len(vectors) != len(texts) or not all(vectors):
        raise SuiteError(f"embedding with {model} returned the wrong number of vectors")
    return vectors


def use_utf8_output() -> None:
    """Make printing any character work, also when Windows would use cp1252."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
