from __future__ import annotations

import logging
from contextvars import ContextVar, Token


_request_id: ContextVar[str] = ContextVar(
    "roamora_request_id",
    default="unavailable",
)


def bind_request_id(request_id: str) -> Token[str]:
    """Bind a validated request ID to the current asynchronous context."""

    return _request_id.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    _request_id.reset(token)


def current_request_id() -> str:
    return _request_id.get()


def log_operation_failure(
    logger: logging.Logger,
    *,
    operation: str,
    error: BaseException,
    level: int = logging.ERROR,
    request_id: str | None = None,
) -> None:
    """Log only stable operational metadata for an exception."""

    logger.log(
        level,
        "operation_failed operation=%s request_id=%s error_type=%s",
        operation,
        request_id or current_request_id(),
        type(error).__name__,
    )
