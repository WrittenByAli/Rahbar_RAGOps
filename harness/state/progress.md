# Handoff log

Append at the end of every session: **done · evidence · open · next**. Newest at the bottom.

## 2026-11-26 - planning session (no assignment feature implemented)
**Done:** read the Assignment 03 PDF (5 pages) and the RAGOps paper (19 pages, figures 2 and 3 and
table 1 viewed as images); cloned the A2 repo at `20b1e98`; wrote the harness and `docs/a3/`.
Moved the A2 `CLAUDE.md` unchanged to `docs/a2/CLAUDE-A2.md`. Nothing committed or pushed.

**Baseline evidence:** API tests 27 passed, indexer tests 9 passed; ruff 0.11.8 reports 10 E402
errors in `eval/offline_eval.py`. No cluster run (Docker daemon stopped).

**Verification loop used on the plan itself:**
1. Quote lint against the PDF, then the verifier on unchanged code. All quotes matched; only `F1.1`
   passed (A2 really ships it). Found a requirement depending on a later phase: fixed.
2. Harness self-tests, then mutation testing: six deliberate defects, six caught.
3. Writing contracts showed the undo check required a new embedding model, which the PDF does not:
   relaxed to the collection, which the PDF does require.
4. Writing the plan showed one pass condition would stall loops on cluster artifacts: split into
   build and evidence stages, with tests.
5. See the final entry below for the closing review pass.

**Open:** decisions in `docs/a3/DECISIONS.md`, including three instructor questions; fill
`harness/members.toml`; hooks not yet observed in a live session.

**Next:** plan phase 0.

### Closing review pass (2026-11-26)
- Reverse coverage (PDF sentences no requirement quotes) found two untracked rules, the
  Kubernetes-only demo and the screenshot rules (now `G.8`, `S.0`), and a risk that a recalibrated
  relevance floor reads as a second change (plan T7.1, instructor question 3).
- Final state: lint 80 requirements, 0 problems, 76 quotes verbatim. Harness tests 51 passed.
  A2 tests 36 passed; no A2 source file changed; A2 submission checker still 0 failures.
- Verifier on unchanged code, full run: 61 FAIL, 1 PASS (`F1.1`), 18 PENDING_HUMAN.
- Hooks and one real build loop (P.1) exercised as subprocesses; the loop ran on a scratch copy.

## 2026-11-26 - repository for Assignment 03
- D-01 decided by one member: new public repo `WrittenByAli/Rahbar_RAGOps`, full A2 history kept,
  A2 head tagged `a2-final`. This commit adds the harness and planning set; no assignment feature.
- **Open:** tell the partner and add them as collaborator; plan T0.2 (`dev` branch, README links,
  `cd.yml` secrets). Public repo: keep roll numbers and the assignment PDFs out of git.
