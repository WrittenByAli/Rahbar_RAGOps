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

## 2026-11-26 - Phase 0: baseline and cluster smoke test (branch `phase-0-baseline`)
**Done:** 10 lint errors fixed; CI lints and tests the harness with pinned ruff and mypy;
`cd.yml` is manual only; README badge and clone URL updated. `P.1` passes.

**Cluster (one laptop only):** existing minikube profile (docker driver, 4 CPUs, 6144 MB, Calico,
Kubernetes 1.37.0). It still runs the A2 deployment in namespace `rahbar`; after resume `/readyz`
was ready with 475 chunks and one question was answered with a correct citation (HTTP 200, 14.1 s).
Pods reached Ollama on the host without setting `OLLAMA_HOST`. Resuming the cluster also started
A2's scheduled reindex job. Docker on this laptop also hosts unrelated projects: never prune.

**Spikes (T0.9), run in a temporary namespace that was then deleted:**
- non-root (uid 1000, `fsGroup`, read-only root) can write, atomically rename and `O_EXCL` lock on
  a new PVC;
- a 100,044-character JSON line comes back from `kubectl logs` as one parseable line;
- Ollama `/api/tags` returns a `digest` per model (use it as the embedding and model version);
- Chroma 1.0.12: see the note under ARCHITECTURE 3.6.

**Corpus:** all 9 documents re-downloaded; every SHA-256 equals the A2 manifest.

**Open (needs members):** the partner's laptop is not verified; `G.4` needs both members' commits.
Python 3.12 is not installed here (tests ran on 3.11).

## 2026-11-26 - Phase 1: measurement fixes and foundations (branch `phase-1-foundations`)
Built by two agents, reviewed by a third read-only agent. **No member has reviewed it yet.**

**Done (`P.2`, `P.3`, `P.4` build stage):** raw citations kept and validity computed from them
(never 1.0 by default); cache keyed by release, collection, index generation, prompt version and
question, with bypass; one versioned prompt template; seed and temperature 0 sent by both
providers (the k8s ConfigMap still forced 0.2: fixed); chunks report the pages of their own text;
heading words are now embedded (some A2 facts existed only in "headings"); chunk ids include
document and version; two A2 bugs in `clean()` that deleted real text fixed.

**Review findings fixed:** cited page now comes from the retrieved chunk, not the model; trace
context carries source, section and page; ADR text corrected.

**Sanity checks, not report numbers:** A2 offline eval on the 6 fixture questions with real
embeddings: recall, citation validity and abstention 1.0. With real `qwen2.5:3b` on the fixtures,
raw citations valid went from 1 of 5 with the old evidence layout to 4 or 5 of 5 with the new one.

**Must be handled before the first baseline:**
- running footers with page numbers are read as headings and shift `page_start` (Phase 2);
- nothing bumps `index_generation` yet (Phase 2);
- set the model context length explicitly (Phase 3, T3.0);
- seeded Ollama calls did **not** always repeat (one run matched 9 of 12): `B.S` must explain it;
- relevance floor 0.55 predates this chunker: recalibrate on non-golden data;
- `eval/run_eval.py` still scores filtered citations (Phase 4);
- ADRs 0006 to 0008 are "Proposed": both members review them.

## 2026-11-26 - resumed: branches are now `feature/ali-<name>`, pushed after each feature
- Pushed `feature/ali-baseline` (248f18c), `feature/ali-foundations` (fd2466d), `feature/ali-ci`.
  `cd.yml` is back to its A2 form (the manual-only change is undone); CI also runs on `feature/**`.
  CI on `feature/ali-ci`: all six jobs passed, including the kind end-to-end job.
- `feature/ali-tracing`: `D1` to `D4` **build stage only**. Clean checkout: 215 API, 59 indexer, 57 eval
  and 51 harness tests passed. Smoke run with real `qwen2.5:3b` and `nomic-embed-text` on the A2
  fixtures, brute-force search and a fake Redis (not a report result, nothing committed): 5 traces
  (answer, cache hit, abstention, injection chunk, model error) passed the D1 field check and
  `trace_tool.py` exited 0 for each. Its builder stopped at a usage limit; there was **no
  independent agent review**.
- Still needed for D: real cluster traces, a trace summary result, the Batch B rail test, D5.
- Data management is unfinished in the working tree (tests exist, `pipeline/ingest.py` does not).

- `feature/ali-batch-b`: `AB.1` files and manifest. Generator run twice with identical output; the
  12 files derived from corpus A are git-ignored; the 6 self-written documents carry a fictional
  marker. `AB.1` is **pending a member's review and signature**. Batch B has not been ingested
  (`AB.2` open). Members: confirm that the generator's short search phrases and own-words rewrite of
  the grading page are acceptable in a public repository.
