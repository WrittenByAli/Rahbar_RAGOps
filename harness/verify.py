"""The harness verifier: the one command that says what is done and what to do next.

    python harness/verify.py                    # every requirement, runs tests too
    python harness/verify.py --part A           # one part
    python harness/verify.py --id A2.1 B.C1     # specific requirements
    python harness/verify.py --fast             # skip pytest/ruff (shows SKIP, never PASS)
    python harness/verify.py --next [--part A]  # the next requirement an agent can work on
    python harness/verify.py --lint --spec "../PDF Assignment_03.pdf"
    python harness/verify.py --render           # regenerate docs/a3/REQUIREMENTS.md

Exit code: 0 only if everything in scope is PASS; 1 otherwise; 2 on a harness error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from checks import (  # noqa: E402
    CHECKS, FAIL, K8S_RULES, PASS, PENDING, SKIP, Context, Result, run_check, stage_of)

ROOT = Path(__file__).resolve().parent.parent
PARTS = ["G", "P", "A", "B", "C", "D", "E", "F", "R", "S"]
PART_NAMES = {
    "G": "General rules", "P": "Plan prerequisites", "A": "Data management",
    "B": "Offline testing", "C": "Change and release gate", "D": "Observability",
    "E": "Drift and coverage", "F": "Kubernetes", "R": "Report", "S": "Screenshots",
}


def load(root: Path = ROOT) -> tuple[dict, list[dict]]:
    data = tomllib.loads((root / "harness" / "requirements.toml").read_text(encoding="utf-8"))
    return data.get("meta", {}), data.get("req", [])


def context(root: Path, meta: dict, fast: bool) -> Context:
    return Context(root=root, fast=fast, base_commit=meta.get("base_commit", ""))


NA = "NA"           # the requirement has no checks in the requested stage
STAGES = ["all", "build", "evidence"]


def specs_for(req: dict, stage: str) -> list[dict]:
    return [c for c in req["checks"] if stage == "all" or stage_of(c) == stage]


def evaluate(ctx: Context, req: dict, stage: str = "all") -> tuple[str, list[Result]]:
    results = [run_check(ctx, spec, req["id"]) for spec in specs_for(req, stage)]
    if not results:
        return NA, results
    statuses = {r.status for r in results}
    for status in (FAIL, SKIP, PENDING):
        if status in statuses:
            return status, results
    return PASS, results


def select(reqs: list[dict], part: str | None, ids: list[str] | None) -> list[dict]:
    chosen = [r for r in reqs
              if (not part or r["part"] == part) and (not ids or r["id"] in ids)]
    unknown = set(ids or []) - {r["id"] for r in reqs}
    if unknown or not chosen:
        raise SystemExit(f"harness error: unknown scope part={part} ids={sorted(unknown)}")
    return chosen


def next_target(ctx: Context, reqs: list[dict], scope: list[dict],
                exclude: set[str] = frozenset(),
                stage: str = "all") -> tuple[dict | None, list[str]]:
    """First failing, agent-workable requirement whose dependencies are met.

    Returns (requirement, blockers). Dependencies count as met when, in the same stage,
    they are PASS, only waiting on a human signature, or have nothing to check.
    """
    by_id = {r["id"]: r for r in reqs}
    memo: dict[str, str] = {}

    def status(rid: str) -> str:
        if rid not in memo:
            memo[rid] = evaluate(ctx, by_id[rid], stage)[0]
        return memo[rid]

    blockers: list[str] = []
    order = {r["id"]: i for i, r in enumerate(reqs)}
    for req in sorted(scope, key=lambda r: (r["phase"], order[r["id"]])):
        if req["kind"] == "human" or req["id"] in exclude:
            continue
        if status(req["id"]) != FAIL:
            continue
        unmet = [d for d in req.get("depends", []) if status(d) not in (PASS, PENDING, NA)]
        if not unmet:
            return req, blockers
        blockers.append(f"{req['id']} waits for {', '.join(unmet)}")
    return None, blockers


# ---------------------------------------------------------------- lint
def _normalise(text: str) -> str:
    text = re.sub(r"Page \| \d+", " ", text)
    text = text.replace("’", "'").replace("‘", "'").replace("•", " ")
    text = text.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def read_spec(path: Path) -> str:
    if path.suffix.lower() != ".pdf":
        return path.read_text(encoding="utf-8")
    try:
        import pymupdf
        with pymupdf.open(path) as doc:
            return "\n".join(page.get_text() for page in doc)
    except ImportError:
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def lint(root: Path, reqs: list[dict], spec_path: Path | None) -> list[str]:
    problems: list[str] = []
    ids = [r.get("id") for r in reqs]
    problems += [f"duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    by_id = {r["id"]: r for r in reqs}
    spec_text = _normalise(read_spec(spec_path)) if spec_path else None
    for req in reqs:
        rid = req["id"]
        for key in ("part", "phase", "kind", "owner", "origin", "title", "checks"):
            if key not in req:
                problems.append(f"{rid}: missing '{key}'")
        if not req.get("checks"):
            problems.append(f"{rid}: no checks, so it could never be verified")
        for spec in req.get("checks", []):
            if spec["type"] != "manual" and spec["type"] not in CHECKS:
                problems.append(f"{rid}: unknown check type {spec['type']}")
            if spec.get("stage") not in (None, "build", "evidence"):
                problems.append(f"{rid}: unknown stage {spec['stage']}")
            if spec["type"] == "k8s" and spec["rule"] not in K8S_RULES:
                problems.append(f"{rid}: unknown k8s rule {spec['rule']}")
        types = {c["type"] for c in req.get("checks", [])}
        expected = ("human" if types == {"manual"} else
                    "mixed" if "manual" in types else "auto")
        if req.get("kind") != expected:
            problems.append(f"{rid}: kind '{req.get('kind')}' but checks imply '{expected}'")
        for dep in req.get("depends", []):
            if dep not in by_id:
                problems.append(f"{rid}: unknown dependency {dep}")
            elif by_id[dep]["phase"] > req["phase"]:
                problems.append(f"{rid} (phase {req['phase']}) depends on later phase {dep}")
        if req.get("origin") == "assignment":
            if not req.get("quote"):
                problems.append(f"{rid}: assignment requirement without a quote")
            elif spec_text is not None and _normalise(req["quote"]) not in spec_text:
                problems.append(f"{rid}: quote not found verbatim in the spec")

    state: dict[str, int] = {}

    def visit(rid: str) -> None:
        if state.get(rid) == 1:
            problems.append(f"dependency cycle through {rid}")
        if rid in state:
            return
        state[rid] = 1
        for dep in by_id[rid].get("depends", []):
            if dep in by_id:
                visit(dep)
        state[rid] = 2

    for rid in by_id:
        visit(rid)

    plan = root / "docs" / "a3" / "IMPLEMENTATION-PLAN.md"
    if plan.is_file():
        text = plan.read_text(encoding="utf-8")
        for rid in by_id:
            if not re.search(rf"(?<![\w.]){re.escape(rid)}(?![\w]|\.\w)", text):
                problems.append(f"{rid}: not referenced in IMPLEMENTATION-PLAN.md")
        for found in sorted(set(re.findall(r"`((?:AB|[GPABCDEFRS])\.?\w*(?:\.\w+)?)`", text))):
            if re.fullmatch(r"(AB\.\d|[GPBRS]\.\w+|[ACDEF]\d(\.\d)?)", found) and found not in by_id:
                problems.append(f"IMPLEMENTATION-PLAN.md cites unknown requirement {found}")
    else:
        problems.append("docs/a3/IMPLEMENTATION-PLAN.md is missing")
    return problems


def render(reqs: list[dict]) -> str:
    out = [
        "# Requirements and acceptance checks",
        "",
        "Generated from `harness/requirements.toml` by `python harness/verify.py --render`.",
        "Do not edit by hand. Status is deliberately not stored here: run the verifier.",
        "",
        "Kind: **auto** = machine check; **mixed** = machine check and human signature; "
        "**human** = only the members can do it.",
    ]
    for part in PARTS:
        rows = [r for r in reqs if r["part"] == part]
        if not rows:
            continue
        out += ["", f"## {part}: {PART_NAMES[part]}", "",
                "| ID | Requirement | Kind | Owner | Phase | Depends on | Checks |",
                "|---|---|---|---|---|---|---|"]
        for r in rows:
            checks = ", ".join(sorted({c["type"] for c in r["checks"]}))
            out.append(f"| {r['id']} | {r['title']} | {r['kind']} | {r['owner']} | {r['phase']} "
                       f"| {', '.join(r.get('depends', [])) or '-'} | {checks} |")
        quotes = [r for r in rows if r.get("quote")]
        if quotes:
            out += ["", "<details><summary>Source quotes (assignment PDF)</summary>", ""]
            out += [f"- **{r['id']}**: \"{r['quote']}\"" for r in quotes]
            out += ["", "</details>"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Assignment 03 requirements.")
    parser.add_argument("--part", choices=PARTS)
    parser.add_argument("--id", nargs="+")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--stage", choices=STAGES, default="all",
                        help="build: code only; evidence: data and run artifacts")
    parser.add_argument("--next", action="store_true")
    parser.add_argument("--lint", action="store_true")
    parser.add_argument("--spec", type=Path, help="assignment PDF, to verify quotes")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--summary", action="store_true", help="counts per part only")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        meta, reqs = load(ROOT)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"harness error: {exc}")
        return 2

    if args.lint:
        problems = lint(ROOT, reqs, args.spec)
        for problem in problems:
            print(f"  LINT  {problem}")
        quoted = sum(1 for r in reqs if r.get("quote"))
        note = f"{quoted} quotes checked against {args.spec.name}" if args.spec else \
            "quotes NOT checked (pass --spec)"
        print(f"lint: {len(reqs)} requirements, {len(problems)} problem(s); {note}")
        return 1 if problems else 0
    if args.render:
        target = ROOT / "docs" / "a3" / "REQUIREMENTS.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(reqs), encoding="utf-8", newline="\n")
        print(f"wrote {target.relative_to(ROOT).as_posix()}")
        return 0

    ctx = context(ROOT, meta, args.fast)
    scope = select(reqs, args.part, args.id)

    if args.next:
        req, blockers = next_target(ctx, reqs, scope, stage=args.stage)
        if req is None:
            print("No agent-workable requirement is ready.")
            for line in blockers[:10]:
                print(f"  blocked: {line}")
            return 1
        _, results = evaluate(ctx, req, args.stage)
        print(f"NEXT {req['id']} (phase {req['phase']}, owner {req['owner']}): {req['title']}")
        if req.get("quote"):
            print(f'  spec: "{req["quote"]}"')
        for spec, result in zip(specs_for(req, args.stage), results):
            print(f"  [{result.status}] {spec['type']}: {result.detail}")
        return 1

    rows = [(req, *evaluate(ctx, req, args.stage)) for req in scope]
    rows = [row for row in rows if row[1] != NA]
    if not rows:
        print(f"nothing to check in stage '{args.stage}' for this scope")
        return 0
    counts: dict[str, int] = {}
    for _, status, _ in rows:
        counts[status] = counts.get(status, 0) + 1

    if args.json:
        print(json.dumps([{"id": r["id"], "status": s,
                           "details": [vars(x) for x in res]} for r, s, res in rows], indent=2))
    elif args.summary:
        for part in PARTS:
            mine = [s for r, s, _ in rows if r["part"] == part]
            if mine:
                print(f"  {part} {PART_NAMES[part]:<24} {mine.count(PASS):>2}/{len(mine):<2} PASS")
    else:
        for req, status, results in rows:
            print(f"{status:<13} {req['id']:<6} {req['title']}")
            if status != PASS:
                for result in results:
                    if result.status != PASS:
                        print(f"                     - {result.detail}")
    if not args.json:
        print("total: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
              + f" (of {len(rows)})")
    if args.stage != "all" and not args.json:
        print(f"note: stage '{args.stage}' only. 'Done' means every stage: run without --stage.")
    return 0 if counts.get(PASS, 0) == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
