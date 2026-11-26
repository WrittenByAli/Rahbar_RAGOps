# ADR 0011: A pattern-based retrieval rail against prompt injection

## Status
Proposed. Implemented; not yet reviewed by both members.

## Context
Assignment 02 had no injection filter: only a prompt instruction and output-schema validation.
The assignment (D4) needs a guardrail whose action is written into the trace. The paper
(section 3.2.3) describes retrieval rails, which exclude or modify retrieved segments before they
reach the model.

## Decision
`api/app/guardrails.py` has two rails.
- **Retrieval rail (`retrieval.injection`).** It reads each chunk that passed the relevance floor.
  A sentence that reads like a command to the model is removed together with the rest of its line.
  If fewer than 10 words would remain, or the match is in the section or source name, the whole
  chunk is dropped.
- **Input rail (`input.injection`).** It only flags the user's question.

Each action records the rail, action, rule id, chunk id, document id and a SHA-256 of the matched
sentence. `RETRIEVAL_RAIL_ENABLED` turns the retrieval rail off, so its effect can be measured, and
the trace says whether it was on.

## Options considered
- **A model that classifies each chunk.** It might catch paraphrases, but it adds a model call per
  chunk, more latency, and a second component that can itself be injected.
- **Dropping every matching chunk.** Simpler, but one planted sentence would remove the real policy
  text around it.
- **Prompt wording only.** It leaves no action to log.

## Consequences
**Easier:** the rules are short enough to explain, and every action is visible in a trace.

**Limits:** these are patterns over English words. A paraphrase, another language or an unusual
spelling gets past them, so a silent rail does not show that a document is safe. Redaction removes
the rest of the line, which can take legitimate text with it. The raw retrieved text of a redacted
chunk is not kept in the trace. Report the rail's measured behaviour, including any miss, rather
than claiming protection.
