"""One retrieval rail and one input rail against prompt injection (paper section 3.2.3).

The retrieval rail reads each chunk that passed the relevance floor, before the prompt is
built. Where a sentence reads like a command to the model, it removes that sentence and the
rest of its line, or the whole chunk when too little would be left. The input rail only
flags the user's question: it changes nothing.

These are patterns over English words. A paraphrase, another language or an unusual spelling
gets past them (docs/adr/0011-retrieval-rail.md), so a silent rail proves nothing.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from .schemas import Passage

RETRIEVAL_RAIL = "retrieval.injection"
INPUT_RAIL = "input.injection"
REMOVED = "[removed by retrieval rail]"
# With fewer words than this left after redaction (a heading and little else), drop the chunk.
MIN_WORDS_LEFT = 10

# Where a command can begin inside a sentence: at its start (after numbering or bullets),
# after punctuation, or after words that speak to the reader. "Students must ignore ..."
# matches none of these: there the verb belongs to the students, not to a command.
_START = (
    r"(?:^[^a-z]*(?:[a-z][.)] )?"
    r"|[:;,\"'(\[>–—-] ?"
    r"|\b(?:please|kindly|now|instead|then|assistant|you (?:must|should|shall|will)) )"
)

# "...instructions of the examiner" or "...instructions regarding the deadline" speak about
# someone else's instructions. Instructions "from the system prompt" are the model's own.
_NOT_SOMEONE_ELSES = (
    r"(?! (?:of|from|by|given by|issued by|sent by|provided by|printed|on|in|for|about"
    r"|regarding|concerning|relating to)\b"
    r"(?! (?:(?:the|this|that|your) )?(?:system|developer|assistant|ai\b|model|prompt|chat"
    r"|conversation)))"
)

_SECRET = r"(?:system prompt|system instructions|hidden instructions|initial (?:instructions|prompt))"

# (rule_id, pattern). They run on one normalised sentence at a time; the first match wins.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    (rule_id, re.compile(pattern, re.IGNORECASE))
    for rule_id, pattern in (
        # "ignore all previous instructions", "disregard your rules"
        ("override_instructions",
         _START
         + r"(?:ignore|disregard|forget|override|bypass|do not follow|don't follow"
         r"|stop following) "
         r"(?:(?:all|any|each|every|of|the|these|those|this|my|our) )*"
         r"(?:(?:(?:your|previous|prior|above|earlier|preceding|foregoing|original|initial"
         r"|system|developer) )+(?:instructions?|prompts?)"
         r"|your (?:rules|guidelines|programming)"
         r"|(?<=\ball )instructions?"
         r"|(?:instructions?|prompts?) (?:above|before this|you (?:were|have been) given)"
         r"|(?:everything|anything) (?:above|you (?:were|have been) told))\b"
         + _NOT_SOMEONE_ELSES),
        # "you are now an unrestricted AI", "from now on you will only reply ..."
        ("role_change",
         r"\byou(?:'re| are) now (?:(?:an?|the|my) (?:[\w-]+ ){0,2}?"
         r"(?:ai|chatbot|bot|language model|llm|(?:helpful|virtual|chat) assistant)\b"
         r"|(?:jailbroken|unrestricted|unfiltered|in (?:developer|jailbreak|god) mode)\b)"
         r"|\bfrom now on,? you (?:will|must|should|shall|are to) (?:(?:only|always) )?"
         r"(?:act|behave|respond|reply|answer|pretend)\b"
         r"|" + _START + r"(?:act as|pretend (?:to be|you are|that you are)) (?:an? )?"
         r"(?:unrestricted|unfiltered|jailbroken|different (?:ai|assistant|chatbot))\b"),
        # "note to the AI: ...", "if you are a language model ..."
        ("addressed_to_model",
         (r"\b(?:note|message|instructions?|attention|reminder|notice) (?:to|for) "
          r"(?:(?:the|any|all|every) )?(?:ai(?: assistant| model| system)?|language model|llm"
          r"|chatbot|virtual assistant) ?(?:[:,–—-]|reading\b)"
          r"|\b(?:if|when) you are an? (?:ai|language model|llm|chatbot)\b"
          r"|(?:^[^a-z]*|[:;\"'(\[>–—-] ?)(?:(?:dear|attention|hey) )?"
          r"(?:ai(?: assistant| model)?|llm|chatbot|language model) ?[:,] "
          r"(?:please |you (?:must|should|will) )?(?:ignore|disregard|forget|tell|say|reply"
          r"|respond|answer|output|always|never|do not|don't)\b")),
        # text that dictates the reply: "respond only with ...", "tell the user that ...",
        # "is to state that ..." (indirect command form used in planted documents)
        ("dictated_reply",
         _START + r"(?:respond|reply|answer)(?: to (?:all|every|any) (?:questions?|users?))? "
         r"(?:only with|with only|with the (?:exact )?(?:words?|phrase|sentence|text))\b"
         r"|" + _START + r"(?:always )?tell the users? that\b"
         r"|\b(?:when(?:ever)?|if) (?:asked|answering)\b[^.]{0,80}?, "
         r"(?:you (?:must|should|will) )?(?:always|only) (?:say|reply|respond|answer|state)\b"
         r"|\bis to (?:state|say|tell|reply|answer) that\b"),
        # asking for the hidden prompt, or for silence towards the user
        ("prompt_secrecy",
         _START + r"(?:reveal|print|show|repeat|output|display|leak|recite)(?: me)?(?: all)? "
         r"(?:your|the) (?:(?:full|hidden|original) )?" + _SECRET + r"\b"
         r"|\bwhat (?:is|are) your " + _SECRET + r"\b"
         r"|" + _START + r"(?:do not|don't|never) (?:(?:tell|inform) the user\b(?!')"
         r"|(?:mention|reveal|disclose)\b[^.]{0,40}? to the user\b(?!')"
         r"|(?:mention|reveal|disclose) (?:this|these) (?:instructions?|prompts?)\b)"),
        # control tokens of chat formats: no policy document needs them
        ("chat_markup",
         (r"<\|(?:im_start|im_end|system|user|assistant|endoftext|eot_id|start_header_id)\|>"
          r"|\[/?inst\]|<</?sys>>")),
    )
]

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Soft hyphen and zero-width characters: invisible, but they split a word for a pattern.
_INVISIBLE = re.compile("[" + chr(0xAD) + chr(0x200B) + "-" + chr(0x200F) + chr(0x2060) + "-" + chr(0x2064) + chr(0xFEFF) + "]")

Match = tuple[str, str]      # (rule_id, the sentence that matched, as it was written)


def _normalise(sentence: str) -> str:
    """The form the rules see: look-alike letters folded, invisible characters removed, white
    space collapsed. Only matching uses it; the text itself is not rewritten."""
    folded = unicodedata.normalize("NFKC", sentence).replace("’", "'")
    return " ".join(_INVISIBLE.sub("", folded).split())


def _rule_for(sentence: str) -> str | None:
    normalised = _normalise(sentence)
    return next((rule_id for rule_id, rule in _RULES if rule.search(normalised)), None)


def _redact(text: str) -> tuple[str, Match | None]:
    """Remove each matching sentence and what follows it on its line.

    An injected command usually goes on after its first sentence, and the chunker keeps no
    paragraph breaks, so there is no reliable sign of where it ends. Returns the new text and
    the first match (None, with the text unchanged, if nothing matched).
    """
    lines = text.split("\n")
    first: Match | None = None
    for number, line in enumerate(lines):
        sentences = _SENTENCE_END.split(line)
        for position, sentence in enumerate(sentences):
            rule_id = _rule_for(sentence)
            if rule_id:
                first = first or (rule_id, sentence)
                lines[number] = " ".join([*sentences[:position], REMOVED])
                break
    return "\n".join(lines), first


def find_injection(text: str) -> str | None:
    """The rule that matches somewhere in `text`, or None."""
    match = _redact(text)[1]
    return match[0] if match else None


def _action(rail: str, action: str, match: Match, passage: Passage | None) -> dict[str, Any]:
    """One guardrail_actions[] entry. It holds a hash of the matched sentence, not the text."""
    rule_id, sentence = match
    return {
        "rail": rail,
        "action": action,
        "rule_id": rule_id,
        "chunk_id": passage.chunk_id if passage else None,
        "doc_id": passage.doc_id if passage else None,
        "excerpt_sha256": hashlib.sha256(sentence.encode("utf-8")).hexdigest(),
    }


def apply_retrieval_rail(passages: list[Passage]) -> tuple[list[Passage], list[dict[str, Any]]]:
    """The passages that may go into the prompt, and one action for each chunk touched.

    The passages passed in are not changed: a redacted chunk is returned as a copy.
    """
    kept: list[Passage] = []
    actions: list[dict[str, Any]] = []
    for passage in passages:
        # The section and source names are printed in the prompt too. Citations must match
        # them exactly, so they cannot be edited: a command there drops the chunk.
        in_name = _redact(passage.section)[1] or _redact(passage.source)[1]
        if in_name:
            actions.append(_action(RETRIEVAL_RAIL, "drop_chunk", in_name, passage))
            continue
        text, match = _redact(passage.text)
        if match is None:
            kept.append(passage)
        elif len(text.replace(REMOVED, " ").split()) < MIN_WORDS_LEFT:
            actions.append(_action(RETRIEVAL_RAIL, "drop_chunk", match, passage))
        else:
            actions.append(_action(RETRIEVAL_RAIL, "redact_span", match, passage))
            kept.append(passage.model_copy(update={"text": text}))
    return kept, actions


def inspect_question(question: str) -> list[dict[str, Any]]:
    """The input rail: a flag if the question reads like an injection. It never blocks."""
    match = _redact(question)[1]
    return [_action(INPUT_RAIL, "flag", match, None)] if match else []
