from __future__ import annotations

import asyncio
import logging
import os

from fastapi import status

from backend.app.core.api_errors import ApiContractException
from backend.app.core.observability import log_operation_failure
from backend.app.models.chat_models import ChatRequest, ChatResponse
from backend.app.services.ai_model_service import generate_travel_response


logger = logging.getLogger(__name__)


def generation_timeout_seconds() -> float:
    try:
        configured = float(
            os.getenv("BACKEND_GENERATION_TIMEOUT_SECONDS", "120")
        )
    except (TypeError, ValueError):
        return 120.0
    return max(1.0, min(configured, 600.0))


async def run_travel_generation(
    chat_request: ChatRequest,
    *,
    request_id: str,
) -> ChatResponse:
    try:
        response = await asyncio.wait_for(
            generate_travel_response(chat_request),
            timeout=generation_timeout_seconds(),
        )
    except TimeoutError as exc:
        logger.warning("Generation timed out request_id=%s", request_id)
        raise ApiContractException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            code="GENERATION_TIMEOUT",
            message="Travel generation took too long. Please try again.",
            retryable=True,
        ) from exc
    except ApiContractException:
        raise
    except Exception as exc:
        log_operation_failure(
            logger,
            operation="travel_generation",
            error=exc,
            request_id=request_id,
        )
        raise ApiContractException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="GENERATION_FAILED",
            message="Travel generation could not be completed.",
            retryable=True,
        ) from exc

    if response is None:
        logger.error("Generation returned no response request_id=%s", request_id)
        raise ApiContractException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="INVALID_GENERATION_RESPONSE",
            message="Travel generation returned an invalid response.",
            retryable=True,
        )

    return response
