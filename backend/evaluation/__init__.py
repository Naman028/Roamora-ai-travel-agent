"""Offline, reproducible evaluation infrastructure for Roamora Base and V1."""

import os
import sys


OFFLINE_ENVIRONMENT_VARIABLES = (
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
)


class EvaluationOfflineModeError(RuntimeError):
    """Raised when imported Hub state prevents evaluator-enforced offline mode."""


def enforce_evaluation_offline() -> None:
    """Force offline mode before any optional Hugging Face library import."""
    for variable in OFFLINE_ENVIRONMENT_VARIABLES:
        os.environ[variable] = "1"
    hub_constants = sys.modules.get("huggingface_hub.constants")
    if hub_constants is not None and not getattr(
        hub_constants,
        "HF_HUB_OFFLINE",
        False,
    ):
        raise EvaluationOfflineModeError(
            "hugging_face_hub_was_imported_online_before_evaluator"
        )


# Python imports the package before ``backend.evaluation.__main__`` or any
# submodule. This is the earliest evaluator-owned point at which the Hub's
# import-time offline constant can be established without changing Roamora.
enforce_evaluation_offline()

from backend.evaluation.contracts import (
    EvaluationCase,
    EvaluationLane,
    EvaluationSuite,
    ModelVariant,
    ProviderFixture,
)

__all__ = [
    "EvaluationCase",
    "EvaluationLane",
    "EvaluationOfflineModeError",
    "EvaluationSuite",
    "ModelVariant",
    "OFFLINE_ENVIRONMENT_VARIABLES",
    "ProviderFixture",
    "enforce_evaluation_offline",
]
