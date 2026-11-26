"""POST /api/retrieve: retrieval on its own, for component tests (docs/adr/0007).

It is not a query: no answer is produced. So it writes no trace line and updates no
rahbar_ query metric; otherwise a test run would change the abstention rate, the cache hit
rate and the similarity distribution of real queries. Like every request, it is still
counted in rahbar_requests_total and logged.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..schemas import RetrieveRequest, RetrieveResponse
from ..services.answer import retrieve_candidates

router = APIRouter(prefix="/api")


@router.post("/retrieve", response_model=RetrieveResponse)
def retrieve(body: RetrieveRequest, request: Request) -> RetrieveResponse:
    state = request.app.state
    return retrieve_candidates(
        body.question, state.retriever, state.settings, body.k or state.settings.top_k
    )
