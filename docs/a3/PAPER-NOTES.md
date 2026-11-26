# Paper notes: RAGOps (Xu, Weytjens, Zhang, Lu, Weber, Zhu, preprint 2026)

Digest of the sections the assignment names, plus what we do with each. It is not a substitute for
reading the paper: both members must be able to explain it (`G.2`). The copy used is marked "not
peer reviewed", and some in-text citations render as "(?)". Say "preprint" when citing it.

## The central claim
A RAG system runs two intertwined lifecycles (section 4, Figure 3):

- **Query processing pipeline**, ordinary DevOps: a Plan, b Build, c Test, d Release, e Deploy,
  f Operate and Monitor, g Analyze and Feedback, looping back to Plan.
- **Data management**, specific to RAGOps: 1 Ingest (self-loop), 2 Verify, 3 Update data lake,
  4 Update retrieval sources, 5 Offline testing, 6 Live testing, 7 Check coverage.

Figure 3 links them: 4 feeds 5, 5 sits under c (Test), 5 leads to 6 (spanning f and g), and 5
leads to 7. LLMOps manages models and prompts; RAGOps adds the changing retrieval sources
(sections 1 and 2.1). Observability goes beyond predefined monitoring: it lets you investigate
"unknown unknowns" from recorded behaviour (section 2.2). `D5` tests exactly that.

## Section 3.2: process view and guardrails
Query path: query enhancement, retrieval planning, embedding, search, reranking, prompt
construction, LLM, response validation. Guardrails sit between components (3.2.3): **I** input,
**D** dialog, **R** retrieval, **O** output. A **retrieval rail** may exclude or modify retrieved
segments, including poisoned content. The paper warns against redundant rails.
Our use: one retrieval rail (`D4`). A2's citation check is an output rail; log it as one.

## Section 4.2: data management

| Paper | Content | Ours | Departure to state in the report |
|---|---|---|---|
| 4.2.1 Ingestion | many sources; includes **updates and deletions**; format conversion such as PDF to text | extract and clean from files delivered to an inbox | pull of files only; no CDC, webhooks or APIs |
| 4.2.2 Verification | **five** checks: quality, completeness, recency, **consistency**, uniqueness | four checks, `A1.2` | **consistency (contradiction detection) is not required by the assignment and not built**; list it under "what is missing" |
| 4.2.3 Data lake | verified single source of truth; archive instead of delete; version every change; stay free of orphans; store access-control metadata | lake, quarantine, archive, versions, `A2` | no access-control metadata |
| 4.2.4 Retrieval sources | incremental chunking and embedding; remove deleted and outdated entries from retrieval | per-document reconcile | - |
| 4.2.5 Offline testing | triggered by pipeline change **or data distribution shift**; module, component, end-to-end; keep old test sets while adding new ones; fix seeds | Part B, `E3`, golden v1 then v2 | no public benchmarks (BEIR, MS MARCO) |
| 4.2.6 Live testing | shadow, A/B, staged roll-out; thresholds trigger a return to offline testing | candidate Deployment tested without user traffic, then rolling update | the candidate receives **no mirrored live traffic**, so it is not true shadow testing; a rolling update is not a user-subset rollout |
| 4.2.7 Coverage | query, retrieval, generation and vocabulary coverage; automate with thresholds; the **85 percent** example | query coverage only (`E2`) | other three kinds not built |

## Table 1: three test levels (the paper uses embedding as the example)

| Level | Tests | Operational metrics | RAG metrics | Required here | Not required |
|---|---|---|---|---|---|
| Module (embedding) | similarity, drift, efficiency | embedding time, memory per embedding | cosine similarity, drift, t-SNE/UMAP | similarity gap, time per chunk, determinism | memory per embedding, t-SNE/UMAP |
| Component (retrieval) | ranking, resilience to noise | retrieval latency, index update speed | MRR, recall@K, precision@K, nDCG | recall@3/5, MRR, nDCG@5, noise test | precision@K, index update speed (cheap to add from ingest reports) |
| End-to-end | faithfulness, hallucination, correctness | full and generation latency | faithfulness, hallucination rate, BLEU, ROUGE, BERTScore | faithfulness, abstention, citation validity, p50/p95 | BLEU, ROUGE, BERTScore |

The note under Table 1 matters: a module change (embedding) affects retrieval and the sources, so
test it at component level and then end to end. A good module score does not guarantee the rest,
which is what short answer (b) asks about.

## Section 5.1 and Table 2: five qualities

| Quality | Paper's design considerations | Candidate measurements for report table 2 |
|---|---|---|
| Adaptability | data-structure construction, granularity configuration | a pipeline change is a new release ConfigMap and collection; chunks re-embedded per change; time to index a candidate |
| Monitorability | detect degradation in accuracy, latency, relevance; detect source changes; clear metrics | gate rules, top-1 similarity distribution, ingest counts of changed/new/deleted, coverage threshold |
| Observability | retrieval: injection detection, relevance; generation: context adherence, toxicity, usefulness | rail actions, faithfulness as context adherence; **toxicity and usefulness are not measured** |
| Traceability | versioning, retrieval routing, grounded source; reconstruct the prompt | trace schema, trace tool, doc versions, collection and embedding version, citations; **single route, so routing is trivial** |
| Reliability | resource usage, bandwidth; the text adds load balancing, autoscaling, prioritisation | measured requests and limits, maxUnavailable 0, probes, rollback; **bandwidth not measured** |

The thresholds and evidence in the report table must come from your own runs.

## Section 6 and 8: open problems you can use honestly
- 6.2: no standard evaluation metrics, and early choices (chunking, embedding) are hard to
  evaluate. Relevant to short answer (d).
- 6.3: it is hard to separate data, model and software quality when monitoring. Relevant to `E3`:
  say which of the three caused each change.
- Section 8: the paper states that its verification phase **has no mechanism to detect malicious
  insertion**. Our injected Batch B document is expected to pass all four checks (confirm this from
  the real report). That bears on short answers (a) and (e).
- Section 6.4 (multimodality), 6.5 and 5.2 (human-in-the-retrieval), and section 7 (use cases) are
  context only. In 7.1.3 the authors build ground truth from certified answers and domain experts,
  then add LLM-generated data: the same human/synthetic split as `B.G2`.
