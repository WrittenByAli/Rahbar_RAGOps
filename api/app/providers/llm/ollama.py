"""Ollama provider (section 3.4)."""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from ...config import Settings
from ...prompting import Prompt, build_prompt
from ...schemas import Answer, Passage
from .base import Generation

log = logging.getLogger("rahbar.ollama")


class OllamaProvider:
    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        prompt = build_prompt(question, passages, self.settings.prompt_version)
        return self.generate_detailed(question, passages, prompt).answer

    def generate_detailed(
        self, question: str, passages: list[Passage], prompt: Prompt
    ) -> Generation:
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

        for attempt in (1, 2):
            # The first try always uses the configured seed, so a run can be repeated. The
            # retry uses the next one: the identical request would likely fail the same way.
            seed = self.settings.llm_seed + attempt - 1
            payload["options"] = {
                "temperature": self.settings.temperature,
                "seed": seed,
                "num_ctx": self.settings.llm_num_ctx,
            }
            raw: str | None = None
            tokens: int | None = None
            try:
                resp = httpx.post(
                    f"{self.settings.ollama_url}/api/chat",
                    json=payload,
                    timeout=120.0,
                )
                resp.raise_for_status()
                body = resp.json()
                tokens = body.get("prompt_eval_count")
                self._check_context_window(tokens)
                raw = body["message"]["content"]
                return Generation(Answer.model_validate_json(raw or ""), raw, attempt, seed, tokens)
            except (ValidationError, KeyError) as exc:
                log.warning("LLM output validation failed", extra={"attempt": attempt, "error": str(exc)})
            except httpx.HTTPError as exc:
                log.error("LLM HTTP error", extra={"error": str(exc)})
                return Generation(
                    Answer(
                        reasoning="HTTP error",
                        answer="The language model is temporarily unavailable.",
                        citations=[],
                        answered_from_context=False,
                        confidence="low",
                    ),
                    attempt=attempt, seed=seed, failure=type(exc).__name__,
                )
        return Generation(
            Answer(
                reasoning="Validation failed after retry",
                answer="I could not produce a valid answer. Please rephrase your question.",
                citations=[],
                answered_from_context=False,
                confidence="low",
            ),
            raw, attempt, seed, tokens, failure="invalid_output",
        )

    def _check_context_window(self, tokens: int | None) -> None:
        # Ollama does not report an error for a prompt that is too long. A count this close
        # to the window means the model may not have seen all of what the trace lists.
        if tokens is not None and tokens >= self.settings.llm_num_ctx:
            log.warning(
                "prompt filled the context window; part of it may have been cut",
                extra={"prompt_eval_count": tokens, "num_ctx": self.settings.llm_num_ctx},
            )
