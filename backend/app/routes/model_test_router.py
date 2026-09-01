from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.app.core.request_controls import generation_request_guard
from backend.app.core.security import require_api_key
from backend.app.models.chat_models import ApiErrorResponse, ChatRequest, ChatResponse
from backend.app.services.generation_service import run_travel_generation


router = APIRouter(tags=["model-test"])


@router.post(
    "/model/test",
    response_model=ChatResponse,
    response_model_exclude_none=True,
    dependencies=[
        Depends(require_api_key),
        Depends(generation_request_guard),
    ],
    responses={
        401: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
        429: {"model": ApiErrorResponse},
        500: {"model": ApiErrorResponse},
        503: {"model": ApiErrorResponse},
        504: {"model": ApiErrorResponse},
    },
)
async def test_model(chat_request: ChatRequest, request: Request) -> ChatResponse:
    request_id = getattr(request.state, "request_id", "unavailable")
    return await run_travel_generation(
        chat_request,
        request_id=request_id,
    )

