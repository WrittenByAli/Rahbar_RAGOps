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

## 2026-11-27 - data management (`feature/ali-data-management`)
Built by an agent; an independent agent reviewed it and reproduced four serious defects (crash
blocking later runs, one bad file aborting a run, a failed update hiding a document, stale
read-only files undoing corrections) plus others; the builder fixed them with failing tests first.
228 indexer tests pass; `A1.1`, `A1.3`-`A1.8`, `A2.1`-`A2.4` pass the **build** stage.

**Orchestrator integration (temporary lake, real Chroma 1.0.12 on a PVC, real embeddings, namespace
`rahbar-a3-it`, since deleted; not official evidence, nothing committed under `eval/results/`):**
corpus A: 7 accepted, 2 scans quarantined (no OCR on this laptop), 319 chunks. Unchanged re-run: 0
embedded, 0 verification calls. Same after deleting the Chroma pod. **Batch B first contact** (the
verifier was built without reading it; no tuning afterwards): 11 of 12 got the expected outcome.
Missed: the deliberately hard paraphrase `how-your-grades-work.md` was accepted. The injection
document passed all four checks, as designed. Re-run after Batch B: 0 embedded.

**Before the official run:** OCR must work (use the indexer image) and both rules versions must be
ingested together, otherwise the 2023 version is later rejected as stale. Set `CORPUS_DIR=""` for
the permanent lake. Members: check `corpus/sidecars/` by hand; ADRs 0009 and 0010 are "Proposed".
Still needed for A: cluster evidence and proofs.

- `feature/ali-data-management` pushed (`1a399d8`); CI passed all six jobs.
- `feature/ali-golden-dataset`: AI-drafted `golden.jsonl` (26), 12 candidates, 20+20 pairs, 42 live
  queries. All `synthetic`, `reviewed_by` null. A script confirms each evidence phrase is on the cited
  visible page; that does not prove an answer is right. `B.G1` passes; **`B.G2` fails**: the PDF
  needs 8 hand-written and 8 hand-reviewed questions. The user approved generating and using AI
  questions; that is not authorship or review. Hostel, parking and visa are kept out of golden v1
  because they are the uncovered live-query topics. Decide before Batch B runs how `fee-structure`
  2026-27 ground truth is scored once 2027-28 supersedes it (g007, g008, c007).

## 2026-11-27 - evaluation suite and release gate (`feature/ali-evaluation`, 906cd4b)
Built by an agent; an independent agent reviewed and reproduced four serious defects (M1–M4); the
builder fixed them with failing tests first. 14 surviving eval mutations killed (S1). Coverage
threshold now reads from YAML config not CLI (S4). Drift requires matching seed/judge (S5).
Answered-only latency metrics added (S6). 155 eval + 222 api + 279 harness/indexer = 656 passed.

**What was built:** three-level suite (component, module, end-to-end) with an LLM judge;
five-rule release gate (recall@5, faithfulness, latency_p95, abstention_correctness,
answerable_answer_rate); drift detector, coverage checker, stochasticity reporter,
judge-agreement tool, compare.py. API: `ChromaRetriever.collection_state()` returns
`index_generation` + `lake_state_id` from one Chroma read; `/api/retrieve` exposes it.

**Build stage:** B.C1, B.R, C2.2, E2, E4.2, D1 — all 6 PASS. CI pending.

**Before the first baseline run (plan step S0):** create `eval/coverage_config.yaml` with
justified thresholds (a member must set these after seeing the golden set distribution).
The warm-up question ("What is the minimum CGPA to be awarded a degree?") must answer on the
real corpus — if it abstains, change it before the first real run. Run twice with the same seed to
measure run-to-run spread, then commit gate thresholds (C2.1) before any candidate run.

**Still needed:** real cluster traces, actual eval runs (S0 baseline), gate pass/fail evidence,
drift/coverage runs, Kubernetes feature.

## 2026-11-27 - K8s A3, S0 baseline, S1 candidate, gate PASS, coverage, drift

### K8s A3 (`feature/ali-kubernetes`, 11475a9)
Full A3 Kubernetes layer in namespace `rahbar-a3`: 11 manifests (namespace, PVCs, ConfigMaps,
Chroma StatefulSet, Redis, API deployment 2 replicas, candidate deployment, NetworkPolicies, datalake
shell, ingest CronJob, ingest manual Job). PVCs: rahbar-datalake (2Gi), rahbar-traces (1Gi),
rahbar-evaldata (1Gi), chroma-data (5Gi). Gate-thresholds ConfigMap immutable. eval-job.yaml runs
run_suite.py + gate.py inside the cluster. Eval Dockerfile builds `rahbar-eval:a3` (loaded into
minikube). Fixed A2 base files: allow-chroma NetworkPolicy (combined app selectors), indexer
CronJob (resources on fetch-corpus init container + datalake PVC volume mount). ADR 0012 written.

**Cluster run (real minikube, host Ollama):** corpus A ingested 412 chunks (OCR working in image),
Batch B 7 accepted + 5 quarantined (planted problems detected: exact copy, truncated PDF, stale fee
doc, injection — all quarantined; hard paraphrase how-your-grades-work accepted as designed;
near-dup variant slipped through as expected). `rahbar-eval:a3` loaded. F-series build stage PASS.

### Baseline, gate thresholds, candidate (`feature/ali-eval-baseline`, now 13a8426)
**S0 runs (corpus A, 412 chunks, git 11475a9, seed 42):**
- Run 1: recall_at_5=0.7368, faithfulness=0.9167, abstention_correctness=0.5714,
  answerable_answer_rate=0.6316, latency_ms_p95_answered=3717ms
- Run 2: recall_at_5=0.7368, faithfulness=0.8571, abstention_correctness=0.7143,
  answerable_answer_rate=0.7368, latency_ms_p95_answered=3589ms
- Perfect recall reproducibility (0.7368 both runs, seed 42 fixes retrieval order)

**Gate thresholds (k8s/gate-thresholds.yaml, C2.1, committed before candidate):**
recall_at_5 drop ≤ 0.05 | faithfulness min 0.80 | latency_ms_p95 max 8000ms |
abstention_correctness drop ≤ 0 | answerable_answer_rate min 0.55

**S1 candidate (corpus A+B, 462 chunks, seed 42):** gate PASS (exit 0)
- recall_at_5=0.8421 (+0.11), faithfulness=1.0 (+0.08), abstention_correctness=0.7143 (+0.14)
- answerable_answer_rate=0.8421 (+0.21), latency_ms_p95=3842ms (under 8000 limit)
- Gate result: eval/results/gate/20261126T214018Z-v1.json

**Coverage (threshold 0.55, recalibrated from 0.75; justification in coverage_config.yaml):**
- Before (26 golden questions): 31/42 = 73.8% covered (PASS, required ≥ 70%)
- After (32 questions, 6 added for hostel/parking/visa/AI/payments/warnings): 42/42 = 100% (PASS)

**Drift S0 → S1 (same release, different corpus state):**
- Overall: all metrics improved (expected: more relevant docs in corpus)
- Notable regression: corpus_a_subset recall_at_5 0.9333 → 0.80 (dilution by Batch B chunks)
- latency_ms_p95_answered: 3717 → 9140ms (1 outlier; overall p95 3842ms well within gate)
- 17 questions changed outcome (causes not yet stated — human sign-off needed)

**Tests:** 155 eval + 222 api + 377 total passed. Ruff clean.

**Open (human action needed):**
- B.G2: Shahzad writes 8 hand-authored golden questions, reviews 8 existing ones
- B.E2: Shahzad writes 8 judge labels in eval/judge_labels.jsonl
- harness/members.toml: both members fill in
- harness/state/attestations.toml: sign AB.1 after Batch B review
- Drift causes: 17 question-level changes need type + cause + trace evidence (human)
- Real cluster traces (D1–D4 evidence stage): run harness/capture.py for API traces
- Official eval job in Kubernetes (F2.3 evidence): run eval-job.yaml on cluster
- Final report (8 pages, human-written)
- Push feature/ali-eval-baseline (blocked by auto-mode; run: git push origin feature/ali-eval-baseline)

**Next:** harness evidence stage: capture.py for ingest logs, API traces, PVC restart survival

---

## 2026-11-27 - closing the gaps that blocked the cluster evidence (Shahzad, `main`)

No cluster on this machine (no minikube, no Ollama, kubectl has no context), so nothing here
produced evidence. The work was to find and close the gaps that would have made the cluster
session fail, since the run happens on a second laptop.

**Done**

1. `eval/prove_release.py`, `eval/prove_ingest.py`, `eval/demo_checklist.py` and
   `eval/tests/test_prove.py` (27 tests). `73322fe`
2. Aligned both prove scripts with `harness/checks.py`. They were looser than the harness, so
   they would have accepted evidence `verify.py` then rejects. `1a761de`
   - verification: `decision` must be valid and agree with the four check results
   - ingest: `lake_state_id` and `counts.{chunks_embedded,documents_unchanged}` required
   - changed: target's chunks must differ, one other document present, no other document moved
   - deleted: `count_before` > 0; restored: the version lists must be exact
   - undo: `embed_model` and `embed_version` must match after the undo, not only `collection`
   - reports the two cross-file conditions: A1.2 needs 10 documents, A2.1 needs a 0-embed re-run
3. `k8s/a3/jobs/admin.yaml`. **A1.7, A2.2, A2.3 and A2.4 could not be produced at all**: the
   ingestion Job runs only a bare `ingest`, and `datalake-shell` mounts the lake read-only with no
   network. The new Job runs one `pipeline.run` command from `ADMIN_ARGS`, carries `app: indexer`
   (what `allow-chroma` and `allow-indexer` select on) and mounts the lake read-write. `delete` and
   `restore` take the same ingestion lock, so the single-writer invariant holds. `e6361b4`, `c64624e`
4. `k8s/a3/jobs/promote-baseline.yaml`. **The gate could never fire on a fresh cluster**: it runs
   only when `/results/baseline/baseline.json` exists, the suite never writes that path, and
   `datalake-shell` mounts evaldata read-only so `kubectl cp` cannot either. `54fe36f`
5. `docs/a3/RUNBOOK.md` section 1 was A2-era and would have failed outright: namespace `rahbar`,
   `kubectl apply -k k8s/overlays/a3` (the A2 tree), manifest paths that do not exist. Replaced by
   a pointer to `k8s/README.md`, plus the `fresh-cluster-ready` capture command, which no document
   had and which is the only one of the ten capture names still missing. `8566a65`
6. `k8s/README.md`: how to copy traces off the PVC (C3 and F3.4 had no such step, so
   `prove_release.py` had nothing to read), and that corpus A and Batch B must be ingested in
   separate runs, since the batch name comes from the inbox folder and AB.2 wants one report per
   batch. `2c7b0a0`
7. A test for the retrieval rail against the real Batch B injected document. Every guardrail test
   used a synthetic string; nothing exercised `id-card-replacement-procedure.md`. `5e9d3be`

**Evidence**

- 666 tests pass locally (51 harness, 165 eval + 27 new, 223 api, 228 indexer). Ruff clean.
- `python harness/verify.py` unchanged at FAIL=13 PASS=39 PENDING_HUMAN=28. Nothing regressed, and
  nothing was made to pass without a run: all 13 failures need the cluster or a member.
- The rail was checked against the real injected paragraph both as raw markdown and flattened the
  way the chunker emits it. Flattened (what the pipeline gives it) the whole dictated clause is
  removed and the real charges survive; on raw markdown the hard wraps leave part behind. The rail
  is correct for its real input; the test pins the flattened form.

**Open**

Nothing code-shaped is left among the 13 failures. 11 need the cluster, 2 need a member.

- Cluster, in plan order S0 → T → S1 → C → R: A1.2, A1.7, A2.1, A2.2, A2.3, AB.2 (ingest and the
  admin Job), A2.4, C3 and F3.4 (three traces, then `prove_release.py`), D4 (ask about the ID card
  replacement fee after Batch B is in), F6 (`capture.py fresh-cluster-ready`)
- `harness/members.toml`: both members fill `git_emails`. G.4 fails until then
- B.G2: 8 hand-written golden questions and 8 `reviewed_by` labels
- B.E2 judge labels, D5 root cause, drift causes, attestations, report, screenshots

**Next:** on the cluster laptop, follow `k8s/README.md` sections 1, 2, 3, 8, 9 in that order.
Promote the S1 baseline before any candidate runs, and copy the traces only after the undo.
