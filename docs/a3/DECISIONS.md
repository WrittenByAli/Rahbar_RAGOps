# Decisions

"Recommended" is the planning default. It is not final until both members confirm it. Record the
confirmation here, and turn the important ones into ADRs under `docs/adr/` while implementing.

**Implementation started on 2026-11-26 using the recommended defaults (D-02 to D-14), which
neither member has confirmed yet.** Each is a configuration value or a contained module, so it can
still be changed. Confirm them before the first official baseline run (plan 1.1, step S0).

| ID | Question | Recommended | Why | Alternatives and cost | Confirmed by |
|---|---|---|---|---|---|
| D-01 | Which repository? | **Decided:** new **public** repo `WrittenByAli/Rahbar_RAGOps`, full A2 history kept; A3 work is everything after `20b1e98` (tag `a2-final`) | keeps both members' history and shows the work builds on A2; `harness` base commit stays valid | private would keep other groups from reading it before submission; continuing in the A2 repo would mix the two assignments | chosen by one member, 2026-11-26; tell the partner |
| D-02 | The one change (`C1`) | Embedding model: `nomic-embed-text` to `mxbai-embed-large` | needs its own collection (`F3.1`); the trace after undo visibly differs (`F3.4`); exercises module, component and end-to-end levels (Table 1) | chunk size or top-k is cheaper, but the embedding version in the undo trace would not change. Needs a model download; a larger model needs more memory. **Risk:** its similarity scores need their own relevance floor, which could be read as a second change (plan T7.1, question 3). Chunk size or top-k avoids that | |
| D-03 | Where does Ollama run? | Host (as A2), **after asking the instructor** | GPU; A2 precedent | in-cluster Ollama: safer reading of "pipeline on Kubernetes", probably CPU-only and slow | |
| D-04 | Document identity and recency | explicit `doc_id` and `doc_version` in sidecars; heuristic only suggests | title heuristics would merge the two Annual Reports | automatic family detection: convenient, error-prone | |
| D-05 | Source model | inbox plus data lake; **explicit** delete | a quarantined update must never delete the good version | mirrored source folder: deletion by absence is risky | |
| D-06 | Near-duplicate method | window embeddings, share of matching windows, fixed verification model | sensitive to long documents; verdicts stable across releases | one mean vector per document: simpler, weaker | |
| D-07 | Release configuration | one immutable ConfigMap per release, referenced by the pod template | `rollout undo` restores it | literal env in the Deployment: also works, noisier | |
| D-08 | Noise test | seeded typos in queries | no write to the vector store | noise documents need the ingestion Job and a separate collection | |
| D-09 | A2 cache | keep as **exact** cache, rename it honestly, namespace it | the semantic path never ran, and a semantic cache adds false hits to evaluation and tracing | wire up semantic matching: needs threshold tuning and more tests | |
| D-10 | HPA | leave it out of the A3 apply path | not required; CPU target is wrong for this service; interferes with rollouts | keep with replicas aligned: more to explain and size | |
| D-11 | Single-writer enforcement | code scan, `Forbid` plus lock, NetworkPolicy; state the limit | meets `F2.2` honestly | add a read-only proxy in front of Chroma: stronger, one more component (optional stretch) | |
| D-12 | Faithfulness judge | a local model at temperature 0, ideally a **different** one from the generator if memory allows | reduces shared blind spots; works offline | same model: less memory, weaker independence. Either way report hand-label agreement | |
| D-13 | Generator model | `qwen2.5:3b` | already present on the planning machine and used for A2's real run | `llama3.2:3b` is the ConfigMap default but was not downloaded | |
| D-14 | Corpus delivery | snapshot once into the inbox; the CronJob does not fetch from the web | reproducible, works offline | A2 init-container fetch: pages drift between runs. Supersedes ADR 0005, so write a new ADR | |

## Questions to put to the instructor
1. May the LLM and embedding server (Ollama) run on the host, outside the cluster, with every
   pipeline component and Job on Kubernetes? (D-03)
2. For "a newer version of the same document is detected": we pass the incoming newer version and
   archive the older one, and we quarantine an incoming **older** version. Is that the intended
   reading of recency "fail"? (`A1.5`, `A1.7`, `A2.4`)

3. `C1` asks for one change. If the change is the embedding model, may its model-specific
   relevance floor be recalibrated as part of that change? (D-02)

## Interpretations we are relying on
These read the assignment where it is silent. State them in the report.
- Recency failure means "stale incoming version". Superseding an active older version is the
  `A2.4` archive path, not quarantine, because it must remain restorable.
- A re-submitted identical copy of the **same** document is "unchanged", not a duplicate.
- "Faithfulness" is measured over answered answerable questions, with `n` reported.
