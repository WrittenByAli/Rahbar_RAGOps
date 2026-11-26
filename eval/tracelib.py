"""Read trace records from JSON Lines files (docs/a3/CONTRACTS.md, "Trace record").

Shared by trace_tool.py and trace_summary.py. Standard library only.

The input may be the per-pod trace files or saved `kubectl logs` output, so lines that are not
trace records are normal: they are counted and skipped.
"""

from __future__ import annotations

import codecs
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_TRACES = Path(__file__).resolve().parent.parent / "traces"

# not_json: a plain log line. broken_trace_line: it says it is a trace but cannot be parsed (cut
# off, or prefixed by `kubectl logs --prefix`). not_a_trace: other JSON, such as a JSON log line.
SKIP_REASONS = ("not_json", "broken_trace_line", "not_a_trace", "no_trace_id", "duplicate")
_SAYS_TRACE = re.compile(r'"kind"\s*:\s*"trace"')


class TraceInputError(Exception):
    """A path given with --traces does not exist or cannot be read."""


@dataclass
class LoadedTraces:
    files: list[Path]
    by_id: dict[str, dict] = field(default_factory=dict)   # in the order first read
    conflicts: set[str] = field(default_factory=set)       # ids with two different records
    skipped: Counter = field(default_factory=Counter)      # keys are SKIP_REASONS


def trace_files(paths: Sequence[str | Path]) -> list[Path]:
    """The files to read: each given file, and every *.jsonl file under each given directory."""
    found: dict[Path, Path] = {}  # keyed by real location, so no file is read twice
    for given in paths:
        path = Path(given)
        if path.is_dir():
            matches = sorted(p for p in path.rglob("*.jsonl") if p.is_file())
        elif path.is_file():
            matches = [path]
        else:
            raise TraceInputError(f"no such trace file or directory: {path}")
        for match in matches:
            found.setdefault(match.resolve(), match)
    return list(found.values())


def read_lines(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise TraceInputError(f"cannot read {path}: {exc}") from exc
    # Windows PowerShell 5.1 saves `kubectl logs ... > file` as UTF-16 with a byte order mark.
    utf16 = data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))
    text = data.decode("utf-16" if utf16 else "utf-8-sig", errors="replace")
    # Split on the newline only. str.splitlines() would also split on characters such as
    # U+2028, which may appear unescaped inside a JSON string that holds document text.
    return text.split("\n")


def _add_line(loaded: LoadedTraces, line: str) -> None:
    if not line:
        return
    try:
        row = json.loads(line)
    except ValueError:
        loaded.skipped["broken_trace_line" if _SAYS_TRACE.search(line) else "not_json"] += 1
        return
    if not isinstance(row, dict) or row.get("kind") != "trace":
        loaded.skipped["not_a_trace"] += 1
        return
    trace_id = row.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id:
        loaded.skipped["no_trace_id"] += 1
        return
    known = loaded.by_id.get(trace_id)
    if known is None:
        loaded.by_id[trace_id] = row
    elif known == row:
        # The API writes each record to stdout and to a file, so reading both is normal.
        loaded.skipped["duplicate"] += 1
    else:
        loaded.conflicts.add(trace_id)


def load_traces(paths: Sequence[str | Path]) -> LoadedTraces:
    loaded = LoadedTraces(files=trace_files(paths))
    for path in loaded.files:
        for line in read_lines(path):
            _add_line(loaded, line.strip())
    return loaded


def as_dict(value: Any) -> dict:
    """The value if it is an object, else {}. Lets a missing or null field read as empty."""
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def cache_status(record: dict) -> str | None:
    return as_dict(record.get("cache")).get("status")


def parse_time(text: Any) -> datetime:
    """An ISO-8601 time that states its zone, converted to UTC. ValueError otherwise."""
    if not isinstance(text, str):
        raise ValueError(f"not a time: {text!r}")
    value = datetime.fromisoformat(text)  # Python 3.11 and later accept a final Z
    if value.tzinfo is None:
        raise ValueError(f"time {text!r} has no zone: end it with Z (UTC) or add e.g. +05:00")
    return value.astimezone(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def use_utf8_output() -> None:
    """Make printing any character work, also when Windows would use cp1252."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
