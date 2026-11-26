"""Ollama provider (section 3.4)."""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from ...config import Settings
from ...prompting import build_prompt
from ...schemas import Answer, Passage

log = logging.getLogger("rahbar.ollama")


class OllamaProvider:
    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        prompt = build_prompt(question, passages, self.settings.prompt_version)
        payload = {
            "model": self.settings.llm_model,
            "messages": prompt.messages(),
            # "format": "json" omitted deliberately: Ollama's grammar-constrained JSON
            # decoding crashes (500, no error body) partway through generation (~380
            # tokens in) with this model on longer, real-evidence prompts - confirmed
            # reproducible by sending the identical request with and without this field.
            # The prompt's own JSON-schema instruction plus Pydantic validation + retry
            # below is the actual enforcement layer; see docs/FAILURE-ANALYSIS.md.
            "stream": False,
        }

        for attempt in range(2):
            # The first try always uses the configured seed, so a run can be repeated. The
            # retry uses the next one: the identical request would likely fail the same way.
            payload["options"] = {
                "temperature": self.settings.temperature,
                "seed": self.settings.llm_seed + attempt,
            }
            try:
                resp = httpx.post(
                    f"{self.settings.ollama_url}/api/chat",
                    json=payload,
                    timeout=120.0,
                )
                resp.raise_for_status()
                content = resp.json()["message"]["content"]
                return Answer.model_validate_json(content)
            except (ValidationError, KeyError) as exc:
                log.warning("LLM output validation failed", extra={"attempt": attempt + 1, "error": str(exc)})
                if attempt == 1:
                    return Answer(
                        reasoning="Validation failed after retry",
                        answer="I could not produce a valid answer. Please rephrase your question.",
                        citations=[],
                        answered_from_context=False,
                        confidence="low",
                    )
            except httpx.HTTPError as exc:
                log.error("LLM HTTP error", extra={"error": str(exc)})
                return Answer(
                    reasoning=f"HTTP error: {exc}",
                    answer="The language model is temporarily unavailable.",
                    citations=[],
                    answered_from_context=False,
                    confidence="low",
                )
        return Answer(reasoning="fallback", answer="Unavailable.", citations=[], answered_from_context=False, confidence="low")
