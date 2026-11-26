"""The LLM provider interface (section 3.5). Swapping providers is one file."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...prompting import Prompt
from ...schemas import Answer, Passage


@dataclass(frozen=True)
class Generation:
    """An answer, and what happened while getting it, for the trace."""

    answer: Answer
    raw_output: str | None = None        # the model's text, exactly as it came back
    attempt: int | None = None           # the attempt that gave `answer`: 1, or 2 for the retry
    seed: int | None = None              # the seed sent with that attempt
    prompt_eval_count: int | None = None   # Ollama's count of the prompt's tokens
    # Set when `answer` is a fallback text and not the model's: "invalid_output", or the
    # type name of the HTTP error. Never an error message: it can quote request headers.
    failure: str | None = None


class LLMProvider(Protocol):
    name: str

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        """Answer the question from the passages, as an `Answer`."""
        ...


def run_provider(
    provider: LLMProvider, question: str, passages: list[Passage], prompt: Prompt
) -> Generation:
    """Generate, with the details when the provider can report them.

    The real providers have generate_detailed(), which sends exactly the `prompt` given: the
    one the trace stores. A provider with only generate() (a test fake) reports nothing more.
    """
    detailed = getattr(provider, "generate_detailed", None)
    if detailed is None:
        return Generation(answer=provider.generate(question, passages))
    return detailed(question, passages, prompt)
