"""A provider with no model and no network.

Use it for every unit test and for CI (section 6): your tests must never call a
real model. It is deterministic: the same input always gives the same output.
"""

from __future__ import annotations

from ...prompting import Prompt
from ...schemas import Answer, Citation, Passage
from .base import Generation


class StubProvider:
    name = "stub"

    def generate_detailed(
        self, question: str, passages: list[Passage], prompt: Prompt
    ) -> Generation:
        """No model reads the prompt. The answer's JSON stands in for the raw output."""
        answer = self.generate(question, passages)
        return Generation(answer, raw_output=answer.model_dump_json(), attempt=1)

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        if not passages:
            return Answer(
                reasoning="stub: nothing was retrieved",
                answer="(stub) No passages were retrieved for this question.",
                citations=[],
                answered_from_context=False,
                confidence="low",
            )
        top = passages[0]
        return Answer(
            reasoning="stub: cites the first retrieved passage",
            answer=f"(stub) A model would answer here from {len(passages)} passages. "
            f"The first one says: {top.text}",
            citations=[Citation(source=top.source, section=top.section, page=top.page)],
            answered_from_context=True,
            confidence="low",
        )
