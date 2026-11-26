"""POST /api/ask. The Ingress sends /api/* here unchanged, so the prefix stays."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..schemas import AskRequest, AskResponse
from ..services.answer import answer_question
from ..trace import new_trace

router = APIRouter(prefix="/api")


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, request: Request) -> AskResponse:
    state = request.app.state
    record = new_trace(body.question, state.settings)
    response = answer_question(
        body.question, state.retriever, state.provider, state.settings,
        bypass_cache=body.bypass_cache, trace=record,
    )
    # The record holds the full prompt, so asking is not enough: the server must allow it.
    if body.include_trace and state.settings.eval_debug_enabled:
        response = response.model_copy(update={"trace": record})
    return response
