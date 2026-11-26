# ADR 0006: Chunks keep heading lines and report the pages of their text

## Status
Proposed · 2026-11-26. Implemented; not yet reviewed by both members. Replaces the overlap and
chunk ID rules (points 4 and 5) of ADR 0002.

## Context
The golden set scores retrieval by page: a chunk matches when the ground-truth page lies in
`[page_start, page_end]`. The Assignment 02 chunker gave every chunk of a section one page (for
the first section, the page of its last line) and carried overlap from one heading into the next.
Its chunk ID used file name, section and position, so edited text kept its ID, and incremental
ingestion compares IDs. It also left heading lines out of chunk text. The detector often takes
the first line of a numbered clause for a heading, so facts in such lines were never embedded.
No A3 baseline exists yet, so changing the chunker now invalidates no A3 result.

## Decision
- **Heading lines are text.** A heading's words (without Markdown `#`) open the first chunk of its
  section, on their own line. They are not repeated: later chunks would have to start their page
  range on the heading's page, and a clause half-line would be pasted into later text.
- **A heading with nothing under it** goes in front of the next text, under that section's label.
  At the end of a document such headings get their own chunk, labelled with the last one.
  `section` labels are otherwise as before. `Introduction` is only a label, never text.
- **Size.** Heading words count like any other word: at most `size` words before overlap, at most
  `size + overlap` with it. Headings are joined to the first paragraph, so an empty line cannot
  strand them. If together they exceed `size` they are cut by word count, even if the paragraph
  alone would fit. After a long run of empty headings, a section's own heading may therefore fall
  in a later chunk.
- **Pages are tracked per word,** headings included. `page_start` and `page_end` are the pages of
  the first and last word of the chunk's own text. `page` equals `page_start` for the API.
- **Overlap** restarts at every heading, and repeats only words on the page where the new chunk's
  own text starts. So it never changes the page range, and every word lies inside the range.
- **Chunk ID** = first 16 hex characters of sha256 over `doc_id`, `doc_version`, section, ordinal
  and the sha256 of the text. The ordinal counts chunks under one heading name. `doc_id` names the
  document across versions; until metadata supplies it, it is the file name.
- `CHUNKER_VERSION` (now `"3"`) must be bumped when chunker output can change.

## Options considered
- **A separate chunk for each empty heading.** Many tiny chunks, and labels that are new.
- **Do not count headings towards `size`.** A run of heading lines would have no upper bound.
- **Let overlap widen the page range.** Truthful, but borrowed words would move the cited page back
  and earn a page match for text that is mostly elsewhere.
- **Forbid chunks that span pages.** This cuts sentences at every page break.
- **One ordinal per document.** One added chunk would renumber, and re-embed, all later ones.
- **Page numbers in the ID.** A change in pagination alone would force re-embedding.

## Consequences
**Easier:** every line the chunker receives is in a chunk, and the page range covers all of it. A
new version or edited text gets new IDs. Changing only `superseded` keeps them.

**Harder, or to watch:**
- With a heading at the foot of a page, the citation shows that page, not the next one.
- A chunk can begin with an empty heading that differs from its label. Labels can still be half
  sentences, because the detector is unchanged.
- A chunk that starts near the top of a page gets less overlap, possibly none.
- Chunk texts and IDs differ from Assignment 02, so its retrieval numbers are historical. Short
  sections no longer borrow words from the section before. The effect on retrieval has not been
  measured.
- This guarantee starts after `clean()`, which removes lines it takes for running headers and
  footers. Two bugs there were fixed with this change, because both deleted real text: on a
  two-page document the threshold was one page, so every line near the top or bottom of either page
  was removed; and a line repeated on **one** page counted as many pages. A line must now be at the
  edge of at least two pages, and of at least half of them.
- Not fixed: a running footer whose page number changes (for example "4 Student Handbook 2026") is
  different on every page, so it is not recognised, is taken for a numbered heading and can put a
  chunk's `page_start` on the footer's page. This has to be fixed before the first baseline.
- Equal IDs do not mean "nothing to do". If only pages moved, update the metadata; if the
  re-embedding fingerprint changed, embed all of that document's chunks again.
- HTML and Markdown files are one page, so for them a page match is only a document match.
