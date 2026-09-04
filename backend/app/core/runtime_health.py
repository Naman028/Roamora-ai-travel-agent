from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, cast, get_args

from pydantic import BaseModel

from backend.app.core.security import configured_api_key, is_production_environment
from backend.app.models.chat_models import ModelVariant


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    failed_checks: tuple[str, ...] = ()


def configured_preload_variant() -> ModelVariant | None:
    """Return the exact configured startup mode without model substitution."""

    value = os.getenv("BACKEND_PRELOAD_MODEL", "fine_tuned").strip()
    if value == "none":
        return None
    if value not in get_args(ModelVariant):
        raise ValueError("invalid_backend_preload_model")
    return cast(ModelVariant, value)


def runtime_readiness(*, lifecycle_ready: bool) -> ReadinessReport:
    """Evaluate cheap provider-neutral readiness checks without generation."""

    failed_checks: list[str] = []
    if not lifecycle_ready:
        failed_checks.append("lifecycle")

    try:
        configured_preload_variant()
    except ValueError:
        failed_checks.append("preload_configuration")

    if is_production_environment() and configured_api_key() is None:
        failed_checks.append("api_key_configuration")

    return ReadinessReport(
        ready=not failed_checks,
        failed_checks=tuple(failed_checks),
    )
