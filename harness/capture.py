"""Run a command and record its real output as evidence.

    python harness/capture.py rollout-status -- kubectl rollout status deployment/api -n rahbar

Writes eval/results/cluster/<name>.txt with the time, command, exit code and git commit,
and prints the same output to the terminal, so you can screenshot it. This is the only
supported way to create cluster evidence files. A report screenshot must still be a real
screenshot taken by a member.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def refuse(reason: str) -> int:
    print(f"capture refused: {reason}", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        print(__doc__)
        return 2
    name, command = argv[0], argv[2:]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        return refuse("name must be lowercase letters, digits and hyphens")
    if any(re.search(r"secret|LLM_API_KEY|token", part, re.I) for part in command):
        return refuse("commands that touch secrets are never recorded")
    try:
        run = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", timeout=900)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return refuse(f"command did not run: {exc}")
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip() or "unknown"
    header = [
        f"# captured: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        f"# command: {subprocess.list2cmdline(command)}",
        f"# exit: {run.returncode}",
        f"# git: {sha}",
    ]
    out = ROOT / "eval" / "results" / "cluster" / f"{name}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(header) + "\n" + run.stdout, encoding="utf-8", newline="\n")
    sys.stdout.write(f"$ {subprocess.list2cmdline(command)}\n{run.stdout}")
    print(f"[exit {run.returncode}; saved to {out.relative_to(ROOT).as_posix()}]")
    return run.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
