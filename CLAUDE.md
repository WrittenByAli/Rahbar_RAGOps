# CLAUDE.md

@AGENTS.md

## Claude Code specifics
`AGENTS.md` above is the shared rule set. This section covers only what Claude Code adds.

**Skills** (`.claude/skills/`)
- `/ragops-loop <part or ids>`: run the bounded build loop on a scope.
- `/ragops-evidence <part>`: walk a member through producing real cluster evidence, one step at a
  time.
- `/ragops-viva <part>`: quiz a member so both can explain every part.

**Subagents** (`.claude/agents/`): `spec-auditor` and `k8s-reviewer` are read-only checkers, so
the one who built something is not the only one who judges it. Use them at phase gates when the
member asks for a review.

**Hooks** (`.claude/settings.json`; they are enforced, not advisory)
- `guard.py` (before tools): denies hand-written evidence, human-only files, new `human` labels,
  identity changes and force-push; freezes the checker during loops; asks before destructive
  cluster or git commands.
- `post_edit.py` (after edits): lint and parse feedback.
- `session_start.py`: loads harness state into the session.
- `stop_loop.py`: only while a loop is active, the verifier decides whether the turn may end.

If a hook blocks you, do not work around it with another tool or a differently spelled command.
Tell the member what was blocked and why you wanted it.
