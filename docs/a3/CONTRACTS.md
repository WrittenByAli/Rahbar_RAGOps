# Artifact contracts

`harness/checks.py` reads exactly these shapes. If a contract must change, change this file, the
check and its test together, outside any loop, with both members' agreement.

Rules for all artifacts:
- Written by a script, never typed.
- File names in result folders start with a UTC stamp, `YYYYMMDDTHHMMSSZ-<label>.json`, because
  "latest" means last in name order.
- Suite results carry provenance: `run_id, started_at, git_sha` (a real commit),
  `golden_sha256, corpus_state_id, release_id`. Commit code **before** an evaluation run, so
  `git_sha` describes what ran. Pass it into the image or Job as an environment variable.

## Inputs (authored)

### `eval/golden.jsonl`
```json
{"id":"g001","question":"...","category":"answerable","hard":false,"hard_reason":null,
 "origin":"synthetic","reviewed_by":null,"reviewed_at":null,"requires_batch":"A",
 "relevant":[{"doc_id":"ug-academic-rules","doc_version":"2026-06","page":7}],
 "expected_answer":"...","added_in":"v1"}
```
`category`: `answerable` | `unanswerable`. `origin`: `human` | `synthetic`. `hard_reason`:
`paraphrase` | `multi_chunk` | `batch_b`. Page ground truth comes from reading the document, not
from retriever output. **Only a member** may set `origin: human` or fill `reviewed_by`.

### Other inputs
| File | One row or object |
|---|---|
| `eval/module_pairs.jsonl` | `{"id","type":"paraphrase"\|"unrelated","a","b"}` |
| `eval/live_queries.jsonl` | `{"id","query","expected":"in_domain"\|"out_of_coverage","topic","authored_by"}` |
| `eval/judge_labels.jsonl` (human only) | `{"trace_id","answer_sha256","human_label":"supported"\|"unsupported","labeller","labelled_at"}` |
| `eval/batch_b/manifest.json` | `{"documents":[{"file","planted","target_doc_id","note"}]}`; `file` relative to the manifest; `planted`: `exact_duplicate`, `near_duplicate`, `truncated_or_corrupt`, `missing_metadata`, `newer_version`, `injection`, `clean` |

## Data management outputs

### `eval/results/verification/<run>.json`
```json
{"run_id":"...","started_at":"...","batch":"B","documents":[
 {"file":"x.pdf","doc_id":"...","doc_version":"...","decision":"quarantined",
  "metadata_source":{"title":"sidecar"},
  "checks":{"quality":{"status":"pass","reason":"..."},
            "completeness":{"status":"pass","reason":"..."},
            "recency":{"status":"pass","reason":"..."},
            "uniqueness":{"status":"fail","reason":"exact copy of ...","score":1.0}}}]}
```
`decision`: `accepted` | `unchanged` | `quarantined`; quarantined if and only if some check failed.
Also write a readable `.md` table beside it for the screenshot.

### `eval/results/ingest/<run>.json`
```json
{"run_id":"...","started_at":"...","collection":"...","lake_state_id":"sha of active manifest at END of run",
 "counts":{"documents_scanned":0,"documents_new":0,"documents_changed":0,"documents_unchanged":0,
   "documents_deleted":0,"documents_superseded":0,"documents_quarantined":0,
   "chunks_embedded":0,"chunks_upserted":0,"chunks_deleted":0,"orphan_chunks":0,
   "verification_embedding_calls":0}}
```
An unchanged run must still **scan** the corpus: `documents_unchanged > 0`, `chunks_embedded = 0`.

### `eval/results/proofs/`
| File | Shape |
|---|---|
| `quarantine.json` | `{"documents":[{"file","reason","in_quarantine":true,"indexed_chunks":0}]}` |
| `changed.json` | `{"changed_doc_id","before":{doc_id:[chunk ids]},"after":{...}}` covering all documents |
| `deleted.json` | `{"doc_id","collection","count_before","count_after":0,"orphan_chunks_after":0}` |
| `restored.json` | `{"doc_id","restored_version","replaced_version","retrieved_versions_before":[replaced],"retrieved_versions_after":[restored],"chunks_embedded":0}` |

Counts and ids must be read back from the vector store, not from the manifest.

## Trace record (one line in `traces/*.jsonl` and on stdout)
```json
{"kind":"trace","schema":1,"trace_id":"...","ts":"...","query":"...","rewritten_query":null,
 "embedding":{"model":"...","version":"<digest>"},
 "collection":{"name":"...","version":"<index_generation>"},
 "retrieved":[{"chunk_id":"...","doc_id":"...","doc_version":"...","page":1,"score":0.71,"rank":1,
               "kept":true,"dropped_by":null}],
 "filters_applied":[{"name":"superseded","value":false},{"name":"relevance_floor","value":0.55,"dropped":[]}],
 "reranker":null,"reranked_order":["chunk ids in the order sent"],
 "context":[{"chunk_id":"...","text":"exact text sent"}],
 "prompt":{"template_version":"p1","sha256":"...","text":"full prompt"},
 "llm":{"model":"...","version":"<digest>","params":{"temperature":0,"seed":42}},
 "response":{"text":"...","raw_output":"...","answered_from_context":true,"confidence":"high"},
 "citations_raw":[],"citations":[],
 "guardrail_actions":[{"rail":"retrieval.injection","action":"drop_chunk","rule_id":"...","chunk_id":"...","doc_id":"..."}],
 "latency_ms":{"cache":0,"embed":0,"retrieve":0,"guardrail":0,"prompt":0,"generate":0,"total":0},
 "cache":{"status":"miss","source_trace_id":null},
 "release_id":"v1","git_sha":"...","pod":"..."}
```
`cache.status`: `hit` | `miss` | `bypass` | `disabled`. If the model is not called, `prompt` and
`context` are present and `null` / `[]`, and `llm.model` still names the configured model.

## Evaluation outputs

### `eval/results/suite/<run>.json`
```json
{"run_id":"...","started_at":"...","git_sha":"...","golden_sha256":"...","corpus_state_id":"...",
 "release_id":"v1","seed":42,"golden_version":"v1","summary_table":"...",
 "module":{"paraphrase_pairs":15,"unrelated_pairs":15,"paraphrase_mean":0,"unrelated_mean":0,"gap":0,
   "embed_ms_per_chunk":0,"determinism_max_abs_diff":0},
 "component":{"recall_at_3":0,"recall_at_5":0,"mrr":0,"ndcg_at_5":0,"n_questions":0,
   "corpus_a_subset":{},"noise":{"method":"typos","seed":42,"noisy":{},
   "delta":{"recall_at_3":0,"recall_at_5":0,"mrr":0,"ndcg_at_5":0}}},
 "end_to_end":{"faithfulness":0,"faithfulness_n":0,"abstention_correctness":0,"abstention_n":0,
   "citation_validity":0,"citation_n":0,"latency_ms_p50":0,"latency_ms_p95":0,"latency_n":0,
   "answerable_answer_rate":0},
 "questions":[{"id":"g001","trace_id":"...","metrics":{},"judge":{"verdict":"...","reason":"..."}}]}
```
A metric that cannot be computed is `null`, never a default of 1.0 or 0.0.

### Metric definitions (put hand-computed examples in `eval/tests/test_metrics.py`)
- A retrieved chunk **matches** a ground-truth item when `doc_id` matches (and `doc_version`, if
  given) and the item's page lies in `[page_start, page_end]`.
- Component metrics use the ranked list **before** the relevance floor. Report the after-floor view
  separately if useful.
- recall@k = ground-truth items matched within the top k / all items; average over answerable
  questions.
- MRR = mean of 1/rank of the first matching chunk, 0 if none in the retrieved list (state the
  list depth).
- nDCG@5: binary gain, awarded only to the **first** chunk that covers each item, so two chunks of
  one page cannot score twice; ideal DCG uses min(items, 5).
- Faithfulness = answers judged supported / answers given on answerable questions. Report `n`.
- Abstention correctness = unanswerable questions declined / unanswerable questions.
- `answerable_answer_rate` = answerable questions answered / answerable questions.
- Citation validity = **raw** model citations that name a chunk sent to the model / all raw
  citations; `null` when there are none.
- Percentiles: state the method (nearest rank) and `n`. With about 30 samples, p95 is nearly the
  maximum. Send one warm-up request first and exclude it. Measure with the cache bypassed.
- HTML and Markdown documents have a single page, so for them "page" recall is only document recall.
  State this limitation.

### Other outputs
| Path | Required keys |
|---|---|
| `stochasticity/` | `run_ids` (two), `seed`, `metrics.<name>.{run1,run2,diff}` for the nine component and end-to-end metrics, `explanation`; report module metrics too |
| `judge_agreement/` | `labelled`, `agreements`, `agreement_rate`, `judge_model` |
| `compare/` | `change`, `baseline` and `candidate`, each with `run_id, release_id, golden_sha256, corpus_state_id, module, component, end_to_end` |
| `gate/` | `decision` `pass`\|`fail`, `exit_code` 0\|1, `rules[]` (`name, baseline, candidate, limit, passed`), `thresholds_sha256`, `candidate_release`, `baseline_run_id`, `candidate_run_id` |
| `trace_summary/` | `window.{since,until}`, `top1_similarity`, `abstention_rate`, `cache_hit_rate`, `latency_ms_p95_by_stage`, `injection_flagged` |
| `coverage/` | `label` `before`\|`after`, `threshold_similarity`, `threshold_justification`, `required_share`, `share_covered`, `golden_count`, `golden_sha256`, `live_queries_sha256`, `matches[]`, `uncovered_topics[]` |
| `drift/` | `before_run_id`, `after_run_id`, `metric_deltas`, `per_question_changes` (each with a stated cause) |
| `resources/` | `api` and `chroma`: `peak_memory_mib, peak_cpu_millicores`; `ingest`, `eval`: `peak_memory_mib`; `method` |
| `release/threshold-flip.json` | `run_a`, `run_b`: `image_id, thresholds_sha256, decision` |
| `release/rejected.json` | `candidate_release`, `gate_decision:"fail"`, `live_release_before`, `live_release_after` |
| `release/undo.json` | `before`, `released`, `after_undo`: `release_id, collection, embed_model, embed_version, trace_id` |

### Gate config: `k8s/gate-thresholds.yaml`
One ConfigMap, `rahbar-gate-thresholds`, whose `data."gate.yaml"` is the only copy of the
thresholds (local runs read the same file). Each rule has `limit` and `justification`:
`recall_at_5` (max drop 0.02 from baseline, the assignment's example), `faithfulness` (minimum),
`latency_ms_p95` (maximum), `abstention_correctness` (not below baseline), plus the guards
`answerable_answer_rate` (minimum) and "no metric is null, question counts match".
Exit 2 if the golden hash or corpus state differs between baseline and candidate.

**Granularity warning to put in the justification:** with about 20 answerable questions, one lost
question moves recall@5 by about 5 points, so a 2-point limit means "lose nothing".

### Cluster evidence: `eval/results/cluster/<name>.txt`
Create only with `python harness/capture.py <name> -- <command>`. Required names:
`pvc-survives-restart`, `ingest-job-logs`, `eval-job-logs`, `eval-job-failed-status`,
`candidate-eval-job-logs`, `rollout-status`, `rollout-undo`, `api-logs-trace`,
`metrics-port-forward`, `fresh-cluster-ready`. These do not replace the report screenshots.

## Required test names
`harness/requirements.toml` lists, per requirement, test files and function names that must exist
and pass. The names are a floor. A test that asserts nothing meaningful will satisfy the checker
and fail the review, so the `spec-auditor` agent and the other member read them.
