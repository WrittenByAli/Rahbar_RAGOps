"""Report and proof files (docs/a3/CONTRACTS.md). Written by the pipeline, never by hand.

    <REPORT_DIR>/verification/<run_id>-<label>.json  and the same name .md   one per run
    <REPORT_DIR>/ingest/<run_id>-<collection>.json                one per run and target
    <REPORT_DIR>/proofs/{quarantine,changed,deleted,restored}.json   the latest proof

A run_id starts with a UTC time stamp, so the latest report is the last name in order.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lake import write_json
from .verify import CHECKS


def new_run_id() -> str:
    """Time stamp, milliseconds (for order within a second) and two random characters."""
    now = datetime.now(timezone.utc)
    return f"{now:%Y%m%dT%H%M%S}Z-{now.microsecond // 1000:03d}{uuid.uuid4().hex[:2]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _label(text: str) -> str:
    """Safe for a file name, and short: the full text is inside the report."""
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "empty"
    if len(label) <= 60:
        return label
    return f"{label[:40]}-{hashlib.sha256(text.encode()).hexdigest()[:12]}"


def _new_file(path: Path, content: str) -> None:
    """Reports are never overwritten: mode "x" fails if the file exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as file:
        file.write(content)


class Reports:
    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)

    def verification(self, report: dict[str, Any]) -> Path:
        path = (self.folder / "verification"
                / f"{report['run_id']}-{_label(report['batch'] or 'no-new-files')}.json")
        _new_file(path, json.dumps(report, indent=2, ensure_ascii=False))
        _new_file(path.with_suffix(".md"), verification_table(report))
        return path

    def ingest(self, report: dict[str, Any]) -> Path:
        path = self.folder / "ingest" / f"{report['run_id']}-{_label(report['collection'])}.json"
        _new_file(path, json.dumps(report, indent=2, ensure_ascii=False))
        return path

    def proof(self, name: str, data: dict[str, Any]) -> Path:
        path = self.folder / "proofs" / f"{name}.json"
        write_json(path, data)
        return path


def _cell(text: object) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def verification_table(report: dict[str, Any]) -> str:
    """The same report as a table that can be read, and shown in a screenshot."""
    documents = report["documents"]
    tally = {d: sum(1 for e in documents if e["decision"] == d)
             for d in ("accepted", "unchanged", "quarantined")}
    lines = [
        f"# Verification report {report['run_id']}", "",
        f"Batch: {report['batch'] or '(no new files)'} | started {report['started_at']} | "
        f"{len(documents)} documents: " + ", ".join(f"{n} {d}" for d, n in tally.items()), "",
        "| File | doc_id | Version | Decision | "
        + " | ".join(name.capitalize() for name in CHECKS) + " |",
        "| --- " * (4 + len(CHECKS)) + "|",
    ]
    for entry in documents:
        cells = [entry["file"], entry["doc_id"], entry["doc_version"] or "-", entry["decision"]]
        for name in CHECKS:
            check = entry["checks"][name]
            cells.append(f"**{check['status'].upper()}**: {check['reason']}")
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    return "\n".join(lines) + "\n"
