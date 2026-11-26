from __future__ import annotations

from ...config import Settings
from ...prompting import check_prompt_version
from .base import LLMProvider


def get_provider(settings: Settings) -> LLMProvider:
    # Checked at startup, so a cache key or trace can never name a template that did not run.
    check_prompt_version(settings.prompt_version)
    name = settings.llm_provider
    if name == "stub":
        from .stub import StubProvider

        return StubProvider()
    if name in ("ollama-host", "ollama-incluster"):
        from .ollama import OllamaProvider

        return OllamaProvider(settings)
    if name == "hosted":
        from .hosted import HostedProvider

        return HostedProvider(settings)
    raise ValueError(
        f"unknown LLM_PROVIDER {name!r}; expected stub, ollama-host, ollama-incluster or hosted"
    )
