---
name: ragops-viva
description: Quiz one group member on a part of Assignment 03 so they can explain it at the demo. Use when a member wants viva practice or after a phase is finished.
argument-hint: "[part letter or topic]"
disable-model-invocation: true
---

# Viva practice: $ARGUMENTS

Goal: both members can explain every part (`G.2`). You are an examiner, not a lecturer.

1. Read the **current** code, manifests and result files for the topic, plus the matching paper
   section in `docs/a3/PAPER-NOTES.md`. Ask only about what actually exists. If the part is not
   implemented, say so and quiz the design.
2. Ask one question at a time and wait. Cover, over the session:
   - *what and where*: which file and function does this?
   - *why*: why this design over the obvious alternative?
   - *failure*: what breaks if this line or object is removed?
   - *numbers*: what does this metric count, over how many samples, and from which result file?
   - *live change*: how would you change this during the demo, and what would you expect?
   - *paper*: which section or table does it come from, and where do we depart?
3. After each answer say what was right, what was missing and where to look (file and line). Do not
   hand over the full answer before they have tried.
4. Do not accept a memorised phrase: follow up with a concrete case from their own results.
5. End with the weak spots as a short list. Do not write an attestation; only a member can.
