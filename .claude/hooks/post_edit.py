"""PostToolUse inner loop: instant feedback on the file just edited.

Python is linted with ruff; JSON, JSONL, TOML and k8s YAML must parse. Exit 2 sends
the message back to the agent so it fixes the file now, not twenty steps later.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path


def problems(path: Path, root: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".py":
        run = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--no-cache", "--output-format=concise",
             str(path)], capture_output=True, text=True, cwd=root, timeout=60)
        if run.returncode == 1:        # 1 = findings; other codes mean ruff is unavailable
            return run.stdout.strip()
        return ""
    text = path.read_text(encoding="utf-8")
    try:
        if suffix == ".json":
            json.loads(text)
        elif suffix == ".jsonl":
            for number, line in enumerate(text.splitlines(), 1):
                if line.strip():
                    try:
                        json.loads(line)
                    except ValueError as exc:
                        return f"line {number}: {exc}"
        elif suffix == ".toml":
            tomllib.loads(text)
        elif suffix in (".yaml", ".yml") and "k8s" in path.parts:
            try:
                import yaml
            except ImportError:
                return ""
            try:
                list(yaml.safe_load_all(text))
            except yaml.YAMLError as exc:
                return str(exc)
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        return str(exc)
    return ""


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".")
        target = Path((payload.get("tool_input") or {}).get("file_path", ""))
        if not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
            return 0
        message = problems(target, root)
    except Exception as exc:  # noqa: BLE001 - feedback is best-effort
        print(f"post_edit.py error: {exc}", file=sys.stderr)
        return 0
    if message:
        print(f"Fix {target.name} before continuing:\n{message[:1500]}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
