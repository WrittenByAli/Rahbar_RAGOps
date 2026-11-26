"""Hosted provider (section 3.4). Key from Secret, never logged."""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from ...config import Settings
from ...prompting import Prompt, build_prompt
from ...schemas import Answer, Passage
from .base import Generation

log = logging.getLogger("rahbar.hosted")


class HostedProvider:
    name = "hosted"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        prompt = build_prompt(question, passages, self.settings.prompt_version)
        return self.generate_detailed(question, passages, prompt).answer

    def generate_detailed(
        self, question: str, passages: list[Passage], prompt: Prompt
    ) -> Generation:
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.llm_model,
            "messages": prompt.messages(),
            "response_format": {"type": "json_object"},
            "temperature": self.settings.temperature,
        }

        for attempt in (1, 2):
            # Same rule as ollama.py: configured seed first, the next seed for the retry.
            seed = self.settings.llm_seed + attempt - 1
            payload["seed"] = seed
            raw: str | None = None
            try:
                resp = httpx.post(
                    f"{self.settings.hosted_base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=120.0,
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"]
                return Generation(Answer.model_validate_json(raw or ""), raw, attempt, seed)
            except (ValidationError, KeyError, IndexError) as exc:
                log.warning("Hosted output validation failed", extra={"attempt": attempt, "error": str(exc)})
            except httpx.HTTPError as exc:
                # Only the type: the message of a header error quotes the header, key included.
                log.error("Hosted HTTP error", extra={"error_type": type(exc).__name__})
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
            raw, attempt, seed, failure="invalid_output",
        )
