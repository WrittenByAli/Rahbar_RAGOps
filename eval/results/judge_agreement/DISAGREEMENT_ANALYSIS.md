# Judge Agreement Disagreement Analysis

**Run:** `20261126T213346Z-s1-batchb-candidate`  
**Judge model:** qwen2.5:3b  
**Labeller:** ali  
**Overall agreement:** 5 of 8 (62%)  
**Disagreements:** 3 cases below — all pattern: human=`unsupported`, judge=`supported`

---

## Disagreement 1 — g002

**Question:** I am two courses behind my study plan. According to the June 2026 undergraduate rules handbook, who can still let me start my final year project, and what does my warning count need to be?

**Answer generated:**
> According to the handbook, a student under academic warning can still be allowed to start their final year project if the Head of Department approves the registration, provided they have not exceeded the maximum allowed courses (five courses) and their warning count is not three or more.

**Human label:** `unsupported`  
**Judge verdict:** `supported`

**Analysis:**  
The answer conflates two different rules. The context (chunk [1]) says a student must have *zero* warning count to register for FYP-I. The answer says warning count must be "not three or more" — that is the threshold for admission cancellation (chunk [5]), not for FYP registration. The answer substitutes the wrong threshold and attributes it to FYP eligibility. The claim about the Head of Department approving registration under warning (chunk [2]) is accurate, but the warning count condition stated in the answer is factually wrong relative to FYP specifically. Human label `unsupported` is correct. The judge failed to catch the specific numeric substitution error.

---

## Disagreement 2 — g010

**Question:** How many library items can an undergraduate student borrow, for how long, and what is the daily fine?

**Answer generated:**
> An undergraduate student can borrow 4 items for a maximum of 5,000 PKR per month. The daily fine for overdue items is 10 PKR per item.

**Human label:** `unsupported`  
**Judge verdict:** `supported`

**Analysis:**  
None of the specific numbers in the answer (4 items, 5,000 PKR/month, 10 PKR/day) appear in the retrieved context. The context chunks cover library conduct rules (p31–p34) and makerspace equipment loans — not library book borrowing limits, loan periods or fines. The answer appears to be hallucinated: the system retrieved the wrong documents and the LLM filled in plausible-sounding numbers. Human label `unsupported` is correct. The judge incorrectly scored this as `supported`, likely because the answer sounds plausible and the judge did not verify that the specific numbers were absent from the context.

---

## Disagreement 3 — g013

**Question:** According to the summary table in the 2023-24 annual report, how many faculty members did the university have in total, and how many of them were in the Professor column?

**Answer generated:**
> The university had a total of 735 faculty members, with 25 of them in the Professor column.

**Human label:** `unsupported`  
**Judge verdict:** `supported`

**Analysis:**  
The only retrieved context chunk is the cover page of the annual report ("ANNUAL REPORT OF THE UNIVERSITY FOR THE YEAR 2023-2024", p1) — it contains no numerical data whatsoever. The specific figures 735 and 25 are not present anywhere in the retrieved context. The answer fabricates statistics from an annual report that was not extracted into the index (the annual report PDF was likely only partially parsed). Human label `unsupported` is correct. This is the clearest case of hallucination: the LLM invented precise numbers from a document whose content was not in the context. The judge scored it `supported` without verifying the numbers were actually in the evidence.

---

## Pattern

All three disagreements follow the same pattern:

| Case | Type | Judge failure |
|------|------|---------------|
| g002 | Wrong numeric threshold substituted | Missed specific fact error |
| g010 | Numbers not in context at all | Plausibility bias |
| g013 | Completely hallucinated statistics | Numbers not verified against evidence |

The judge (qwen2.5:3b) tends to score answers as `supported` when the answer *sounds* plausible given the topic of the retrieved documents, even when the specific claims are absent from or contradict the evidence. Human review catches these cases because a human checks whether each specific fact appears in the context, rather than whether the answer is topically consistent.

This 62% agreement rate indicates the judge should not be used as the sole arbiter of faithfulness without human spot-checks, particularly for answers that cite specific numbers or thresholds.
