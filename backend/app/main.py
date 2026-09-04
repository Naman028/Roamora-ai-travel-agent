from __future__ import annotations

import os
import logging
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Import config first to ensure environment is loaded
from backend.app.core.api_errors import ApiContractException
from backend.app.core.config import load_dotenv_and_get_env
from backend.app.core.observability import (
    bind_request_id,
    log_operation_failure,
    reset_request_id,
)
from backend.app.core.runtime_health import (
    LivenessResponse,
    ReadinessResponse,
    configured_preload_variant,
    runtime_readiness,
)
from backend.app.models.chat_models import ApiError, ApiErrorResponse, FieldError
from backend.app.routes.chat_router import router as chat_router
from backend.app.routes.model_test_router import router as model_test_router
from backend.app.routes.api_services_router import router as api_services_router
from backend.app.services.ai_model_service import preload_model, unload_models


logger = logging.getLogger(__name__)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def _load_backend_env() -> None:
    # Use centralized config loading
    load_dotenv_and_get_env()
    return


def _cors_origins() -> list[str]:
    configured = os.getenv("BACKEND_CORS_ORIGINS", "").strip()
    if not configured:
        return ["http://localhost:3000", "http://127.0.0.1:3000"]
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        return response


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied
            if REQUEST_ID_PATTERN.fullmatch(supplied)
            else uuid.uuid4().hex
        )
        request.state.request_id = request_id
        request_id_token = bind_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(request_id_token)
        response.headers["X-Request-ID"] = request_id
        return response


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else uuid.uuid4().hex


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
    field_errors: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    payload = ApiErrorResponse(
        error=ApiError(
            code=code,
            message=message,
            request_id=request_id,
            retryable=retryable,
            field_errors=field_errors or [],
        )
    )
    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=response_headers,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_backend_env()
    app.state.lifecycle_ready = False
    preload_variant = configured_preload_variant()
    if preload_variant is not None:
        logger.info("runtime_starting preload_mode=%s", preload_variant)
        preload_model(preload_variant)
        logger.info("runtime_started model_preloaded=true")
    else:
        logger.info("runtime_started model_preloaded=false")

    app.state.lifecycle_ready = True
    try:
        yield
    finally:
        app.state.lifecycle_ready = False
        logger.info("runtime_stopping")
        unload_models()

app = FastAPI(title="Wanderly Model Backend", version="0.2.0", lifespan=lifespan)
app.state.lifecycle_ready = False

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


@app.exception_handler(ApiContractException)
async def api_contract_exception_handler(
    request: Request, exc: ApiContractException
) -> JSONResponse:
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.public_message,
        retryable=exc.retryable,
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    field_errors = [
        FieldError(
            field=".".join(str(part) for part in error["loc"]),
            message=str(error["msg"]),
        )
        for error in exc.errors()
    ]
    return _error_response(
        request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="VALIDATION_ERROR",
        message="The request did not pass validation.",
        retryable=False,
        field_errors=field_errors,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    codes = {
        400: ("BAD_REQUEST", "The request could not be processed."),
        401: ("UNAUTHORIZED", "Authentication is required."),
        403: ("FORBIDDEN", "This operation is not allowed."),
        404: ("NOT_FOUND", "The requested resource was not found."),
        409: ("CONFLICT", "The request conflicts with the current state."),
        413: ("REQUEST_TOO_LARGE", "The request is too large."),
        429: ("RATE_LIMITED", "Too many requests. Please try again shortly."),
        501: ("NOT_IMPLEMENTED", "This operation is not available."),
        503: ("SERVICE_UNAVAILABLE", "The service is temporarily unavailable."),
        504: ("GATEWAY_TIMEOUT", "The service took too long to respond."),
    }
    code, message = codes.get(
        exc.status_code,
        ("HTTP_ERROR", "The request could not be completed."),
    )
    return _error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message=message,
        retryable=exc.status_code in {408, 429, 500, 502, 503, 504},
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    request_id = _request_id(request)
    log_operation_failure(
        logger,
        operation="unhandled_request",
        error=exc,
        request_id=request_id,
    )
    return _error_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        message="The service encountered an unexpected error.",
        retryable=True,
    )

app.include_router(chat_router)
app.include_router(model_test_router, prefix="/api")
app.include_router(api_services_router, prefix="/api")


@app.get("/health", response_model=LivenessResponse)
def health() -> LivenessResponse:
    """Report process liveness without checking providers or the model."""

    return LivenessResponse()


@app.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
def ready(request: Request) -> ReadinessResponse | JSONResponse:
    """Report cheap runtime readiness without invoking generation."""

    report = runtime_readiness(
        lifecycle_ready=bool(getattr(request.app.state, "lifecycle_ready", False))
    )
    response = ReadinessResponse(
        status="ready" if report.ready else "not_ready"
    )
    if report.ready:
        return response
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=response.model_dump(mode="json"),
    )

