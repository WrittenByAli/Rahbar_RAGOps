"""Every threshold of the verification stage, in one place.

How they were chosen, with the numbers: docs/adr/0009-verification-checks.md. They were
set on corpus A only. Changing a near-duplicate value means running
indexer/tools/calibrate_near_dup.py again.
"""

# ---- quality: measured on the text as extracted
MIN_CHARS_PER_PAGE = 100            # average non-space characters per page
EMPTY_PAGE_CHARS = 20               # a page with fewer non-space characters has no text
MAX_EMPTY_PAGE_SHARE = 0.5          # share of pages without text
MAX_UNREADABLE_CHAR_SHARE = 0.05    # replacement, unprintable and "(cid:NN)" characters
MIN_WORDLIKE_SHARE = 0.6            # share of tokens that look like words or numbers

# ---- uniqueness: near-duplicates by embedding (D-06)
WINDOW_WORDS = 100                  # words per window
WINDOW_STRIDE = 50                  # a new window starts every this many words
NEAR_DUP_SIMILARITY = 0.90          # cosine at or above this: two windows match
NEAR_DUP_SHARE = 0.70               # near-duplicate when this share of windows match

# ---- only a suggestion, never a decision: "this may be a new version of X"
SUGGEST_SHARE = 0.60               # above the highest share between unrelated documents
