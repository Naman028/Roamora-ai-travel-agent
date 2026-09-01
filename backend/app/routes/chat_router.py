from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from backend.app.core.api_errors import ApiContractException
from backend.app.core.request_controls import generation_request_guard
from backend.app.core.security import require_api_key
from backend.app.models.chat_models import (
    ApiErrorResponse,
    ChatRequest,
    ChatResponse,
)
from backend.app.services.generation_service import run_travel_generation


router = APIRouter(tags=["chat"])

CHAT_ERROR_RESPONSES = {
    401: {"model": ApiErrorResponse, "description": "Invalid or missing API key"},
    422: {"model": ApiErrorResponse, "description": "Request validation failed"},
    429: {"model": ApiErrorResponse, "description": "Rate or concurrency limit"},
    500: {"model": ApiErrorResponse, "description": "Generation failed"},
    502: {"model": ApiErrorResponse, "description": "Invalid generation response"},
    503: {"model": ApiErrorResponse, "description": "Backend is misconfigured"},
    504: {"model": ApiErrorResponse, "description": "Generation timed out"},
}


@router.get("/models")
def list_models() -> dict[str, object]:
    return {
        "models": [
            {"id": "base", "label": "Base Llama 3.2 3B"},
            {"id": "fine_tuned", "label": "Fine-tuned Wanderly LoRA"},
        ],
        "default": "fine_tuned",
    }


@router.get("/chat", include_in_schema=False)
async def disabled_chat_get() -> None:
    raise ApiContractException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        code="METHOD_NOT_ALLOWED",
        message="Chat prompts must be sent with POST /chat.",
        retryable=False,
        headers={"Allow": "POST"},
    )


@router.post(
    "/chat",
    response_model=ChatResponse,
    response_model_exclude_none=True,
    dependencies=[
        Depends(require_api_key),
        Depends(generation_request_guard),
    ],
    responses=CHAT_ERROR_RESPONSES,
)
async def chat_post(chat_request: ChatRequest, request: Request) -> ChatResponse:
    request_id = getattr(request.state, "request_id", "unavailable")
    return await run_travel_generation(
        chat_request,
        request_id=request_id,
    )
