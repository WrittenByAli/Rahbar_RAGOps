# Gap analysis: Assignment 02 code against Assignment 03

Based on commit `20b1e98` (head of `main`). "Confirmed" means the line was read or the command was
run. "Risk" means it follows from the design but has not been reproduced on a cluster.

## Baseline health

| Item | Result | How checked |
|---|---|---|
| API tests | 27 passed | pytest, temporary venv, Python 3.11 (project targets 3.12) |
| Indexer tests | 9 passed | same |
| Lint | **10 errors**, all E402 in `eval/offline_eval.py:38-47` | ruff 0.11.8 (the pinned version). CI lints `eval/` too |
| Cluster behaviour | **not checked** | Docker daemon was not running during planning |

## Defects that would corrupt Assignment 03 results

Fix these before producing any baseline number.

### F-01 Citation validity is true by construction (confirmed)
`api/app/services/answer.py:57` removes citations that are not in the retrieved set, and `:77`
returns only the survivors. `eval/run_eval.py:64-72` then measures validity on that filtered list.
It can only be 1.0 or "no citations". The 1.0 in `eval/results.json` says nothing about the model.
**Fix:** keep `citations_raw` in the trace and measure validity on it (`P.2`).

### F-02 The "semantic" cache only does exact matching (confirmed)
`answer.py:27` calls `cache.get(question)` and `:92` calls `cache.put(question, resp)`, both without
a vector. The similarity branch (`cache.py:38-51`) is unreachable from the application. Only unit
tests reach it. `docs/PIPELINE.md` and `CACHE_THRESHOLD` describe behaviour that never runs.
**Decision needed:** wire it up, or call it an exact cache (D-09).

### F-03 Candidate and live would share cache entries (confirmed key, risk on cluster)
The key is `sha256(question.lower())` only (`cache.py:25-26`). In F3 both Deployments use the same
Redis, so a candidate evaluation could be answered with **live's cached answers**. The gate would
then compare live with itself. After `rollout undo`, a "new trace" could also be a cached answer
from the release just reverted.
**Fix:** namespace the key with release id, collection, index generation and prompt version, and
let the evaluation bypass the cache (`P.3`).

### F-04 Nothing invalidates the cache after ingestion (confirmed)
`flush()` has no caller outside tests, and entries live for one hour. A deleted, superseded or
changed document can still be answered from cache. **Fix:** covered by `P.3` (index generation in
the key).

### F-05 Ingestion rebuilds the whole index (confirmed)
`indexer/pipeline/store.py:28-40` deletes and recreates the collection. `run.py:76-83` embeds every
chunk on every run. This contradicts `A2.1` to `A2.4`. **Fix:** per-document reconcile.

### F-06 Page numbers are wrong for sections that span pages (confirmed by reading)
`chunk.py:56-84` gives a section the page where its heading appeared, and every chunk of that
section inherits it (`:30-34`). The golden set needs ground-truth **page**, so page-level recall
would be scored against wrong numbers. Overlap also crosses section boundaries (`:149-152`).
**Fix before any baseline** (`P.4`). Changing the chunker afterwards invalidates earlier results.

### F-07 The old golden labels are circular (confirmed in the file header)
`eval/golden.yaml:1-12` says each `(source, section)` label was copied from what the live
retriever returned (except the one it missed). Labels taken from the system under test favour it.
**Fix:** take page-level ground truth for `eval/golden.jsonl` from the documents themselves. Do not
carry the A2 labels across, and do not relabel A2 questions as `human` unless a member really wrote
them by hand.

### F-08 The old gate can pass a broken pipeline (confirmed)
`run_eval.py:112-113` returns 1.0 for citation validity when there are no citations, and 1.0 for
abstention when there are no abstention questions. A pipeline that always abstains scores perfect
abstention and perfect citation validity. Only recall would catch it, and recall ignores
generation. **Fix:** a missing metric is a failure, and the gate also checks the answer rate on
answerable questions (`C2.2`).

### F-09 There is no injection filter or rail (confirmed by search)
No guardrail, filter or rail exists under `api/` or `indexer/`. The only mitigations are
indirect: the system prompt says to answer only from the evidence, and the model output must match
a schema. Neither inspects retrieved text or logs an action. The only related test,
`api/tests/test_starter.py:84`, uses the stub provider, so it cannot show that injected text is
resisted. `docs/ALI-FINAL-REPORT.md:40` marks this PASS. `D4` therefore means **building** a
retrieval rail, which the assignment explicitly allows ("or one simple retrieval rail").

### F-10 Rollback would not roll back configuration (risk, Kubernetes behaviour)
Collection and embedding model come from the mutable ConfigMap `rahbar-config` via `envFrom`
(`k8s/base/api-deployment.yaml:27-31`). `kubectl rollout undo` restores only the **pod template**.
If a release edits that ConfigMap, undo changes nothing and the `F3.4` demo fails.
**Fix:** one immutable ConfigMap per release, referenced by name in the pod template.

### F-11 Other items

| # | Finding | Where | Effect |
|---|---|---|---|
| a | Chroma admits **every** pod in the namespace | `k8s/base/networkpolicy.yaml`, `allow-chroma`, `podSelector: {}` | weakens the single-writer claim (`F2.2`) |
| b | NetworkPolicy is enforced only with a policy CNI | `Makefile` uses `--cni=calico`; ADR 0005 notes kind does not enforce | start minikube with Calico or policies are decoration |
| c | No policy lets an eval pod reach the API | `allow-api` ingress admits only `ingress-nginx` | the eval Job would time out |
| d | HPA uses CPU with `minReplicas: 3` | `k8s/base/hpa.yaml` | contradicts the A2 notes; fights rollouts. `.github/workflows/cd.yml` had to delete it |
| e | Corpus is re-downloaded on every CronJob run | `indexer-cronjob.yaml` init container | web pages change between fetches and the demo would need internet |
| f | Chroma used 667 Mi of a 1 Gi limit with one collection | `docs/evidence/kubectl-top.txt` | a second collection plus Batch B risks OOMKill; measure first |
| g | `TEMPERATURE=0.2`, no seed | `k8s/base/configmap.yaml`, providers | `B.S` needs a fixed seed. A retry with identical seed and temperature 0 would repeat itself |
| h | Prompt template duplicated | `providers/llm/ollama.py` and `hosted.py` | prompt version and hash need one source |
| i | `/readyz` requires Redis | `routes/health.py:53-62` | candidate and traces depend on Redis being up |
| j | Two relevance-floor defaults (0.55 and 0.3) | `api/app/config.py:50` and `:70` | the effective default is 0.3 |
| k | Floor 0.55 was calibrated for `nomic-embed-text` | `config.py:48-50` | another embedding model needs its own floor, set on data other than the golden set |

## Do not reuse these A2 numbers

`docs/ENGINEERING-NOTES.md` question 5 reports recall@5 of 0.90 / 0.75 / 0.83 by question type.
`eval/results.json` holds 0.875 / 1.0 / 0.667. The note also quotes thresholds (1.50 against 1.70)
that differ from the golden set (2.00). Question 4 attributes about 1.5 Gi of embedding memory to
the indexer pod, but embedding runs in Ollama outside that pod, and no committed measurement
supports the figure. The A3 report must take **every** number from new, committed A3 result files
(`G.3`). If these A2 documents stay in the submitted repository, expect questions about them.

## Requirement coverage today

The verifier is authoritative. At planning time, on the unchanged A2 code: **1 PASS** (`F1.1`),
the rest FAIL or awaiting a human.

| Part | What A2 provides | What is missing |
|---|---|---|
| A | extract (PDF, OCR, HTML, MD), clean, structure-aware chunking, `superseded` flag and query filter | verification stage, data lake, quarantine, incremental update, delete, archive and restore, reports, Batch B |
| B | recall@k and gate skeleton, offline evaluator, 17 questions | JSONL golden set with page ground truth and provenance, module tests, MRR, nDCG, noise, faithfulness, judge agreement, latency percentiles, seeds, timestamped results |
| C | exit-code gate in `run_eval.py` | threshold config, non-vacuous rules, compare report, two candidates |
| D | JSON logs with request id, two Prometheus metrics, chunk ids in one log line | trace schema, prompt capture, trace tool, five metric families, window summary, rail |
| E | nothing | all of it |
| F | Dockerfiles, API Deployment (maxUnavailable 0), Chroma StatefulSet and PVC, Services, CronJob, NetworkPolicies | data-lake, traces and eval PVCs, ingestion Job without `--dummy`, eval Job, threshold ConfigMap, candidate stack, versioned releases, measured resources, `k8s/README.md` |

## Environment observed on the planning machine (2026-11-26)

15.2 GB RAM, 16 logical CPUs, NVIDIA RTX 4060 Laptop GPU. Free disk: **C: 13 GB**, D: 35 GB.
Docker 29.8.0 installed but the daemon was stopped. minikube 1.39.0, kubectl 1.36.1, Ollama 0.40.4
with `nomic-embed-text` and `qwen2.5:3b` pulled (**not** `llama3.2:3b`, which the base ConfigMap
names). Python 3.11.9. `make`, `helm` and `kind` are not on PATH, so no required command may depend
on `make`. Checking Ollama's model list started the Ollama desktop service. The other member's
machine was not examined.
