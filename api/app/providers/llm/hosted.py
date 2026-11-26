"""Hosted provider (section 3.4). Key from Secret, never logged."""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from ...config import Settings
from ...prompting import build_prompt
from ...schemas import Answer, Passage

log = logging.getLogger("rahbar.hosted")


class HostedProvider:
    name = "hosted"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        prompt = build_prompt(question, passages, self.settings.prompt_version)
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

        for attempt in range(2):
            # Same rule as ollama.py: configured seed first, the next seed for the retry.
            payload["seed"] = self.settings.llm_seed + attempt
            try:
                resp = httpx.post(
                    f"{self.settings.hosted_base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=120.0,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return Answer.model_validate_json(content)
            except (ValidationError, KeyError, IndexError) as exc:
                log.warning("Hosted output validation failed", extra={"attempt": attempt + 1, "error": str(exc)})
                if attempt == 1:
                    return Answer(
                        reasoning="Validation failed after retry",
                        answer="I could not produce a valid answer. Please rephrase your question.",
                        citations=[],
                        answered_from_context=False,
                        confidence="low",
                    )
            except httpx.HTTPError as exc:
                log.error("Hosted HTTP error", extra={"error": str(exc)})
                return Answer(
                    reasoning=f"HTTP error: {exc}",
                    answer="The language model is temporarily unavailable.",
                    citations=[],
                    answered_from_context=False,
                    confidence="low",
                )
        return Answer(reasoning="fallback", answer="Unavailable.", citations=[], answered_from_context=False, confidence="low")
