"""Shared test setup: no test may reach the network or write trace files by accident."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app import model_versions


@pytest.fixture(autouse=True)
def isolated_from_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    # A trace asks Ollama for model digests with httpx.get. Without this, a test that uses the
    # chroma settings would make a real network call. Tests that need digests replace it again.
    def no_network(*args: Any, **kwargs: Any) -> None:
        raise httpx.ConnectError("tests have no network")

    monkeypatch.setattr(httpx, "get", no_network)
    model_versions.clear_cache()
    # A developer's own environment must not make tests write files or change behaviour.
    for name in ("TRACE_DIR", "EVAL_DEBUG_ENABLED", "RETRIEVAL_RAIL_ENABLED"):
        monkeypatch.delenv(name, raising=False)
