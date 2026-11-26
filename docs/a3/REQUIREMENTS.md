# Requirements and acceptance checks

Generated from `harness/requirements.toml` by `python harness/verify.py --render`.
Do not edit by hand. Status is deliberately not stored here: run the verifier.

Kind: **auto** = machine check; **mixed** = machine check and human signature; **human** = only the members can do it.

## G: General rules

| ID | Requirement | Kind | Owner | Phase | Depends on | Checks |
|---|---|---|---|---|---|---|
| G.1 | Requirements are not changed | human | both | 10 | - | manual |
| G.2 | Both members can explain every part | human | both | 10 | - | manual |
| G.3 | Every reported number comes from own runs; result files committed | mixed | both | 10 | - | manual, tracked |
| G.4 | Commits come from both members | auto | both | 0 | - | git_authors |
| G.5 | Results and traces committed under eval/results/ and traces/; manifests under k8s/ | mixed | both | 10 | - | manual, tracked |
| G.6 | README gives the exact command for each part A to F | auto | M2 | 10 | - | readme_parts |
| G.7 | Demo rehearsal: eval Job, open a trace, ingest an unseen batch via the ingestion Job | human | both | 10 | F2.1, F2.3, D2 | manual |
| G.8 | Demo runs on Kubernetes; Docker-only earns 0 | human | both | 10 | F1.1 | manual |

<details><summary>Source quotes (assignment PDF)</summary>

- **G.1**: "There will be no credit if the given requirements are changed."
- **G.2**: "Both members must be able to explain every part of the work at the demo."
- **G.3**: "Every number in your report must come from runs of your own pipeline, and the result files must be committed to your repository."
- **G.4**: "Commits must come from both members."
- **G.5**: "all result files, trace samples and reports produced by your scripts are committed under"
- **G.6**: "the repository README gives the exact command to run each part (A to F)."
- **G.7**: "During the demo you will run your evaluation as a Kubernetes Job, open a trace, and ingest a small batch of documents you have not seen by running your ingestion Job."
- **G.8**: "The pipeline must be deployed on Kubernetes during the demo. If it is run on Docker containers only (for example plain docker run or Docker Compose), the demo will receive 0."

</details>

## P: Plan prerequisites

| ID | Requirement | Kind | Owner | Phase | Depends on | Checks |
|---|---|---|---|---|---|---|
| P.1 | A2 baseline is green: lint and all existing tests | auto | M2 | 0 | - | command |
| P.2 | Raw model citations are kept so citation validity is not true by construction | auto | M1 | 1 | P.1 | tests |
| P.3 | Cache is namespaced per release and index state, and can be bypassed | auto | M1 | 1 | P.1 | tests |
| P.4 | Chunks carry the page range their text really came from | auto | M1 | 1 | P.1 | tests |

## A: Data management

| ID | Requirement | Kind | Owner | Phase | Depends on | Checks |
|---|---|---|---|---|---|---|
| A1.1 | Verification stage runs after ingestion and before chunking | auto | M1 | 2 | P.4 | tests |
| A1.2 | Pass or fail with a reason for all four checks, for every document | auto | M1 | 2 | A1.1 | verification_report |
| A1.3 | Quality check | auto | M1 | 2 | A1.1 | tests |
| A1.4 | Completeness check | auto | M1 | 2 | A1.1 | tests |
| A1.5 | Recency check | auto | M1 | 2 | A1.1 | tests |
| A1.6 | Uniqueness check: exact by hash, near-duplicate by embedding similarity | auto | M1 | 2 | A1.1 | tests |
| A1.7 | Failed documents are quarantined and not indexed | auto | M1 | 2 | A1.2 | proof, tests |
| A1.8 | One verification report file per run | auto | M1 | 2 | A1.2 | tests |
| A2.1 | Unchanged corpus re-run embeds zero chunks | auto | M1 | 2 | A1.1 | ingest_zero, tests |
| A2.2 | A changed document replaces only its own chunks | auto | M1 | 2 | A2.1 | proof, tests |
| A2.3 | A deleted document leaves no orphan chunks | auto | M1 | 2 | A2.1 | proof, tests |
| A2.4 | Superseded version archived, excluded through metadata, restorable | auto | M1 | 2 | A2.1 | proof, tests |
| AB.1 | Batch B: at least 10 documents with the planted problems | mixed | both | 2 | - | batch_b, manual |
| AB.2 | Both batches run through A1 and A2 | auto | M1 | 6 | AB.1, A1.2, A2.4 | verification_report |

<details><summary>Source quotes (assignment PDF)</summary>

- **A1.1**: "Add a step that runs after ingestion and before chunking."
- **A1.2**: "For every document it records pass or fail, with a reason, for each of four checks"
- **A1.3**: "quality (empty, garbled or failed extraction)"
- **A1.4**: "completeness (required metadata present: source, title, date or version, page count; text not truncated)"
- **A1.5**: "recency (a newer version of the same document is detected)"
- **A1.6**: "uniqueness (exact duplicates by hash, near-duplicates by embedding similarity)"
- **A1.7**: "Documents that fail are moved to a quarantine folder and are not indexed."
- **A1.8**: "The output is a verification report file per run."
- **A2.1**: "Re-running ingestion on an unchanged corpus must embed zero chunks (show the counts)."
- **A2.2**: "A changed document replaces only its own chunks."
- **A2.3**: "A deleted document leaves no orphan chunks in the vector store (prove it with a count by document id)."
- **A2.4**: "A superseded version is archived and excluded from retrieval through metadata, and you can restore it (rollback demo)."
- **AB.1**: "Create a second batch of at least 10 documents with planted problems: at least 2 exact duplicates, 1 near-duplicate, 1 truncated or corrupt file, 1 with missing metadata, 1 newer version of an existing document, and 1 containing an injected instruction."
- **AB.2**: "Run both batches through A1 and A2."

</details>

## B: Offline testing

| ID | Requirement | Kind | Owner | Phase | Depends on | Checks |
|---|---|---|---|---|---|---|
| B.G1 | Golden set size and composition | auto | both | 4 | P.4 | golden |
| B.G2 | Human-written and human-reviewed questions | mixed | both | 4 | - | golden, manual |
| B.M1 | Module: paraphrase vs unrelated cosine similarity and the gap | auto | M1 | 4 | - | pairs, result |
| B.M2 | Module: embedding time per chunk | auto | M1 | 4 | - | result |
| B.M3 | Module: determinism | auto | M1 | 4 | - | result |
| B.C1 | Component: recall@3, recall@5, MRR, nDCG@5 | auto | M1 | 4 | B.G1 | result, tests |
| B.C2 | Component: noise test with per-metric change | auto | M1 | 4 | B.C1 | result |
| B.E1 | End-to-end: faithfulness, abstention, citation validity, latency p50/p95 | auto | M2 | 4 | B.G1, P.2, P.3 | result |
| B.E2 | At least 8 hand labels and judge agreement | mixed | both | 4 | B.E1 | contains, manual, result |
| B.S | Fixed seeds, two runs, per-metric difference and explanation | auto | M2 | 4 | B.E1, B.C1 | stochasticity |
| B.R | One command runs the suite and writes a timestamped result with a summary table | auto | M2 | 4 | B.C1, B.E1 | result, tests |

<details><summary>Source quotes (assignment PDF)</summary>

- **B.G1**: "At least 25 questions in eval/golden.jsonl: at least 15 answerable (with ground-truth document and page), at least 5 unanswerable (the correct behaviour is to abstain), and at least 5 hard (paraphrased, or needing more than one chunk, or from Batch B)."
- **B.G2**: "At least 8 questions are written by hand by the group. Each question is labelled human or synthetic, and at least 8 synthetic questions are reviewed by hand."
- **B.M1**: "Mean cosine similarity for at least 15 paraphrase pairs against at least 15 unrelated pairs, and the gap between them."
- **B.M2**: "Embedding time per chunk."
- **B.M3**: "embed the same text twice and report the largest difference."
- **B.C1**: "recall@3, recall@5, MRR and nDCG@5 on the golden set."
- **B.C2**: "add at least 3 irrelevant documents, or add typos to the queries, and report how each metric changes."
- **B.E1**: "Faithfulness (is the answer supported by the retrieved context), abstention correctness on the unanswerable questions, citation validity, and latency p50 and p95."
- **B.E2**: "label at least 8 answers by hand and report how often the judge agrees with you."
- **B.S**: "Fix random seeds and run the whole suite twice. Report the difference for each metric and explain any remaining variation."
- **B.R**: "One command runs the whole suite and writes a timestamped result file to eval/results/ with a summary table."

</details>

## C: Change and release gate

| ID | Requirement | Kind | Owner | Phase | Depends on | Checks |
|---|---|---|---|---|---|---|
| C1 | One pipeline change compared at all three levels | auto | M2 | 7 | B.R | compare |
| C2.1 | Thresholds in a config file, each value justified, fixed before the candidate run | mixed | M2 | 5 | B.S | contains, manual |
| C2.2 | Gate script exits pass or fail | auto | M2 | 4 | B.R | tests |
| C2.3 | One failing and one passing candidate | auto | M2 | 7 | C2.2, C1 | gate_results |
| C3 | Candidate released and rolled back on Kubernetes | auto | M2 | 8 | F3.2, F3.3, F3.4 | proof |

<details><summary>Source quotes (assignment PDF)</summary>

- **C1**: "Run the Part B suite on the baseline and on the candidate and compare all three levels."
- **C2.1**: "Write thresholds in a config file"
- **C2.2**: "A script applies the gate and exits with pass or fail."
- **C2.3**: "Show one candidate that fails (a deliberately bad one is fine) and one that passes."
- **C3**: "The candidate is released and, if needed, rolled back on Kubernetes."

</details>

## D: Observability

| ID | Requirement | Kind | Owner | Phase | Depends on | Checks |
|---|---|---|---|---|---|---|
| D1 | One complete JSON trace line per query | auto | M1 | 3 | P.2, P.3 | jsonl_fields, tests |
| D2 | Trace tool prints the exact context and prompt | auto | M1 | 3 | D1 | tests |
| D3 | Metrics and time-window summary script | auto | M1 | 3 | D1 | contains, result, tests |
| D4 | Guardrail action logged in the trace, tested with the Batch B injected document | auto | M1 | 6 | D1, AB.1 | guardrail_evidence, tests |
| D5 | Root cause of one wrong or abstained answer, from its trace only | human | both | 6 | D2, B.E1 | manual |

<details><summary>Source quotes (assignment PDF)</summary>

- **D1**: "Every query writes one JSON line containing: trace id, timestamp, user query, any rewritten query, embedding model and version, collection name or version, retrieved chunk ids with document ids, document versions and scores, filters applied, reranked order, a prompt hash (or the full prompt), model and parameters, response, citations, guardrail actions, latency per stage, and cache hit or miss."
- **D2**: "A command takes a trace id and prints the exact context and prompt that produced that answer."
- **D3**: "top-1 retrieval similarity distribution, abstention rate, cache hit rate, p95 latency per stage, and number of injection attempts flagged. A script summarizes the trace log over a time window."
- **D4**: "writes its action into the trace. Test it with the injected document from Batch B."
- **D5**: "Pick one wrong or abstained answer from Part B. Using only its trace, find the cause (which chunk, document version or filter), and write 5 lines in the report."

</details>

## E: Drift and coverage

| ID | Requirement | Kind | Owner | Phase | Depends on | Checks |
|---|---|---|---|---|---|---|
| E1 | At least 40 live queries, about 28 in domain and at least 12 uncovered | mixed | M2 | 9 | - | live_queries, manual |
| E2 | Query coverage with justified threshold and grouped uncovered topics | mixed | M2 | 9 | E1, B.G1 | manual, result, tests |
| E3 | Data drift: ingest Batch B, re-run the suite, report what changed and why | mixed | M2 | 6 | AB.2, B.R | manual, result |
| E4.1 | At least 6 new golden questions from uncovered topics; coverage rises | mixed | both | 9 | E2 | coverage_rise, manual |
| E4.2 | Coverage script exits non-zero below threshold | auto | M2 | 9 | E2 | tests |

<details><summary>Source quotes (assignment PDF)</summary>

- **E1**: "Create at least 40 queries as if from users: about 28 in the domain of your golden set and at least 12 from a topic your corpus or tests do not cover."
- **E2**: "For each live query find the most similar golden question. Report the share above a similarity threshold you choose and justify"
- **E3**: "Ingest Batch B, re-run the Part B suite, and report what changed and why."
- **E4.1**: "Add at least 6 new golden questions from the uncovered topics, re-run, and show that coverage rises."
- **E4.2**: "A script returns a non-zero exit code when coverage falls below your threshold."

</details>

## F: Kubernetes

| ID | Requirement | Kind | Owner | Phase | Depends on | Checks |
|---|---|---|---|---|---|---|
| F1.1 | API Deployment, vector store StatefulSet with PVC, API Service | auto | M2 | 5 | - | k8s |
| F1.2 | Data lake, quarantine and archive on a PVC that survives restarts | auto | M2 | 5 | F1.1 | capture, k8s |
| F2.1 | Ingestion Job with verification, plus a CronJob | auto | M2 | 5 | A2.1, F1.2 | capture, k8s |
| F2.2 | The ingestion Job is the only writer to the vector store | auto | M2 | 5 | F2.1 | forbid, k8s, tests |
| F2.3 | Eval Job writes to a volume and fails when the gate fails | auto | M2 | 5 | B.R, C2.2, C2.1 | capture, k8s |
| F2.4 | Gate thresholds are a ConfigMap; editing one flips the outcome without a rebuild | auto | M2 | 8 | F2.3 | proof |
| F3.1 | Candidate as a second Deployment, Service and collection; suite run against it | auto | M2 | 8 | F2.3, C2.2 | capture, k8s |
| F3.2 | Rolling update with maxUnavailable 0, watched with rollout status | auto | M2 | 8 | F3.1, C2.3 | capture, k8s |
| F3.3 | A rejected candidate is never rolled out; a bad release is undone | auto | M2 | 8 | F3.2 | capture, proof |
| F3.4 | After undo, a new trace shows the previous collection and embedding version | auto | M2 | 8 | F3.3, D1 | proof |
| F4.1 | Traces on stdout, readable with kubectl logs | auto | M2 | 5 | D1, F1.1 | capture |
| F4.2 | Traces also written to a volume | auto | M2 | 5 | D1, F1.1 | k8s |
| F4.3 | Metrics endpoint reachable with port-forward | auto | M2 | 5 | D3, F1.1 | capture |
| F5 | Requests and limits from measurements | mixed | M2 | 8 | F2.3 | k8s, manual, result |
| F6 | A fresh cluster reaches a working pipeline from k8s/ | mixed | M2 | 10 | F2.1, F2.3 | capture, contains, manual |

<details><summary>Source quotes (assignment PDF)</summary>

- **F1.1**: "The API runs as a Deployment, the vector store as a StatefulSet (or a Deployment with a PersistentVolumeClaim), and a Service exposes the API."
- **F1.2**: "The data lake, quarantine folder and archived document versions live on a PersistentVolumeClaim, so they survive pod restarts."
- **F2.1**: "Ingestion with verification (Part A) runs as a Kubernetes Job, and a CronJob re-runs it on a schedule."
- **F2.2**: "The only writer to the vector store is that Job."
- **F2.3**: "The Part B suite runs as a Job (kubectl apply -f k8s/eval-job.yaml), writes its result file to a volume, and exits with a failing status when the release gate (C2) fails."
- **F2.4**: "The gate thresholds (C2) are a ConfigMap, and you show that changing a threshold changes the outcome without rebuilding the image."
- **F3.1**: "The candidate from Part C runs as a second Deployment with its own Service and its own collection name, and the Part B suite Job is run against it."
- **F3.2**: "release it to the live Deployment with a rolling update (maxUnavailable: 0), watched with kubectl rollout status."
- **F3.3**: "Show a candidate that is stopped by the gate and never rolled out, and a bad release that you reverse with kubectl rollout undo."
- **F3.4**: "After the undo, a new trace must show the previous collection and embedding version."
- **F4.1**: "Trace records go to standard output as JSON lines and are readable with kubectl logs."
- **F4.2**: "They are also written to a volume for the report scripts."
- **F4.3**: "The metrics endpoint is reachable with kubectl port-forward."
- **F5**: "Set resource requests and limits for the API, the vector store and the Jobs, from measurements you take (copy the numbers into the report)."
- **F6**: "A fresh cluster must reach a working pipeline by applying them, with any extra commands listed in a k8s/README.md."

</details>

## R: Report

| ID | Requirement | Kind | Owner | Phase | Depends on | Checks |
|---|---|---|---|---|---|---|
| R.0 | Report: one PDF, at most 8 pages plus screenshot appendix; names and roll numbers first | human | both | 10 | - | manual |
| R.1 | Figure 3 stage mapping table | human | both | 10 | - | manual |
| R.2 | Table of the five qualities | human | both | 10 | - | manual |
| R.3 | Results tables, figures and the release decision | human | both | 10 | - | manual |
| R.4 | Short answers (a) to (f) | human | both | 10 | - | manual |
| R.5 | Contribution table and 2 to 3 line AI statement | human | both | 10 | - | manual |

<details><summary>Source quotes (assignment PDF)</summary>

- **R.0**: "Report: one PDF, at most 8 pages, with the screenshots listed under Submission added as an appendix"
- **R.1**: "A table that maps each stage of Figure 3"
- **R.2**: "what you measure, your threshold, and the evidence."
- **R.3**: "the release decision for Part C (release or reject, with reasons)."
- **R.4**: "(a) Which verification check caught the most problems in Batch B, and which problem did you not catch? What would catch it?"
- **R.5**: "A contribution table (what each member did) and an AI statement of 2 to 3 lines."

</details>

## S: Screenshots

| ID | Requirement | Kind | Owner | Phase | Depends on | Checks |
|---|---|---|---|---|---|---|
| S.0 | Every screenshot: command and output, uncropped, labelled, own cluster and runs | human | both | 10 | - | manual |
| S.A | Screenshots A | human | M1 | 10 | - | manual |
| S.B | Screenshot B | human | M2 | 10 | - | manual |
| S.C | Screenshots C | human | M2 | 10 | - | manual |
| S.D | Screenshots D | human | M1 | 10 | - | manual |
| S.E | Screenshots E | human | M2 | 10 | - | manual |
| S.F | Screenshots F | human | M2 | 10 | - | manual |

<details><summary>Source quotes (assignment PDF)</summary>

- **S.0**: "Each screenshot shows the command you ran and its output, in the terminal, with nothing cropped. Label it with the part and item (for example "B3"). Screenshots must come from your own cluster and your own runs."
- **S.A**: "the verification report for Batch B (pass or fail per check, with reasons); a second ingestion run on an unchanged corpus showing 0 chunks embedded."
- **S.B**: "the evaluation summary with all three levels."
- **S.C**: "the release gate failing and the release gate passing."
- **S.D**: "one full trace record; the trace tool rebuilding the prompt for a trace id."
- **S.E**: "the coverage output before and after adding the new questions."
- **S.F**: "for your namespace; the logs of a completed ingestion Job and a completed evaluation Job; the gate result changing after a ConfigMap edit;"

</details>
