"""PreToolUse guard: the integrity rules in AGENTS.md, enforced mechanically.

Denies: hand-writing generated evidence, writing human-only artifacts, adding "human"
or "reviewed" labels to the golden set, moving the goalposts during a loop, and
history or identity tampering. Asks the user first for destructive cluster or git
operations.

This is a guardrail against mistakes and shortcuts, not a security boundary: shell
commands can be written in ways no pattern catches. On an internal error it lets the
call through and says so on stderr.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

GENERATED = ("eval/results/", "traces/")
HUMAN_ONLY = ("harness/state/attestations.toml", "eval/judge_labels.jsonl", "harness/members.toml")
LOOP_FROZEN = ("harness/", ".claude/", "k8s/gate-thresholds.yaml")
LOOP_WRITABLE = ("harness/state/progress.md",)
HUMAN_MARKERS = (r'"origin"\s*:\s*"human"', r'"reviewed_by"\s*:\s*"[^"]+"')

SHELL_DENY = [
    (r"\bgit\b[^|;&\n]*\bpush\b[^|;&\n]*(\s--force\b|\s-f\b|\s--force-with-lease\b)",
     "force-push rewrites shared history and the other member's commits"),
    (r"\bgit\b[^|;&\n]*\bcommit\b[^|;&\n]*(--author\b|--no-verify\b)",
     "commits must be made under each member's own identity, with checks on"),
    (r"\bgit\s+config\b[^|;&\n]*\buser\.(name|email)\s+\S",
     "an agent must not change the git identity (G.4: both members commit)"),
    (r">>?\s*[\"']?[^\s\"'|;&>]*(eval[\\/]+results|(?<![\w-])traces)[\\/]",
     "evidence is written only by the pipeline scripts or harness/capture.py"),
    (r"\b(Set-Content|Add-Content|Out-File|New-Item|tee|sed\s+-i)\b[^|;&\n]*"
     r"(eval[\\/]+results[\\/]|(?<![\w-])traces[\\/])",
     "evidence is written only by the pipeline scripts or harness/capture.py"),
    (r"(>>?\s*[\"']?[^\s\"'|;&>]*|\b(Set-Content|Add-Content|Out-File|New-Item|tee|sed\s+-i|cp|mv|"
     r"rm|del|Copy-Item|Move-Item|Remove-Item)\b[^|;&\n]*)(attestations\.toml|judge_labels\.jsonl|"
     r"members\.toml)",
     "that file is human-only"),
]
SHELL_ASK = [
    (r"\bminikube\s+delete\b", "deletes the whole cluster and its volumes"),
    (r"\bkubectl\b[^|;&\n]*\bdelete\b[^|;&\n]*\b(ns|namespace|namespaces|pvc|persistentvolumeclaim"
     r"|statefulset|sts)\b", "can destroy the data lake or vector index"),
    (r"\bkubectl\b[^|;&\n]*\bdelete\b[^|;&\n]*\s-[fk]\s", "bulk delete from manifests"),
    (r"\bgit\b[^|;&\n]*\b(reset\s+--hard|clean\s+-\w*f|filter-branch|rebase)\b",
     "discards or rewrites work"),
    (r"\bdocker\s+(system|volume)\s+prune\b", "deletes Docker data"),
]


def relative(path: str, root: Path) -> str:
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    except ValueError:        # a different drive on Windows
        return path.replace("\\", "/").lower()
    return rel.replace("\\", "/").lower()


def count_markers(text: str) -> int:
    return sum(len(re.findall(p, text or "")) for p in HUMAN_MARKERS)


def adds_human_label(tool: str, tool_input: dict) -> bool:
    if tool == "Write":
        path = Path(tool_input.get("file_path", ""))
        old = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        return count_markers(tool_input.get("content", "")) > count_markers(old)
    new = tool_input.get("new_string", tool_input.get("new_source", ""))
    return count_markers(new) > count_markers(tool_input.get("old_string", ""))


def loop_active(root: Path) -> bool:
    try:
        state = json.loads((root / "harness" / "state" / "loop.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(state.get("active"))


def decide(payload: dict, root: Path) -> tuple[str, str] | None:
    """Return ('deny'|'ask', reason), or None to leave the call to normal permissions."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if not target:
            return None
        rel = relative(target, root)
        if rel.startswith(GENERATED):
            return "deny", (f"{rel} is generated evidence. Run the script that produces it "
                            "(or harness/capture.py). Never type results by hand.")
        if rel in HUMAN_ONLY:
            return "deny", f"{rel} is human-only. Ask the member to edit it themselves."
        if rel == "eval/golden.jsonl" and adds_human_label(tool, tool_input):
            return "deny", ("An agent must not mark a golden question as human-written or "
                            "human-reviewed. The member adds that label themselves.")
        if (loop_active(root) and rel.startswith(LOOP_FROZEN) and rel not in LOOP_WRITABLE):
            return "deny", (f"{rel} is frozen while a loop runs: the agent may not edit its own "
                            "checks or thresholds. Stop the loop and ask the members.")
        return None

    if tool in ("Bash", "PowerShell"):
        command = tool_input.get("command", "")
        for pattern, reason in SHELL_DENY:
            if re.search(pattern, command, re.I):
                return "deny", f"Blocked: {reason}."
        for pattern, reason in SHELL_ASK:
            if re.search(pattern, command, re.I):
                return "ask", f"Confirm: this {reason}."
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".")
        verdict = decide(payload, root)
    except Exception as exc:  # noqa: BLE001 - a broken guard must not brick the session
        print(f"guard.py error, not enforced for this call: {exc}", file=sys.stderr)
        return 0
    if verdict:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": verdict[0],
            "permissionDecisionReason": verdict[1],
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
