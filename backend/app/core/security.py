from __future__ import annotations

import os
import secrets

from fastapi import Depends, status
from fastapi.security import APIKeyHeader

from backend.app.core.api_errors import ApiContractException


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def configured_api_key() -> str | None:
    """Return the shared key used to protect expensive backend endpoints."""

    value = os.getenv("BACKEND_API_KEY") or os.getenv("WANDERLY_API_KEY")
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def is_production_environment() -> bool:
    value = os.getenv("BACKEND_ENV") or os.getenv("ENVIRONMENT") or "development"
    return value.strip().lower() in {"production", "prod"}


async def require_api_key(
    supplied_api_key: str | None = Depends(api_key_header),
) -> None:
    expected = configured_api_key()
    if expected is None:
        if is_production_environment():
            raise ApiContractException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="BACKEND_MISCONFIGURED",
                message="The service is not configured to accept protected requests.",
                retryable=False,
            )
        return

    if not supplied_api_key or not secrets.compare_digest(
        supplied_api_key, expected
    ):
        raise ApiContractException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_API_KEY",
            message="Invalid or missing API key.",
            retryable=False,
        )
