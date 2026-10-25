from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from backend.evaluation import enforce_evaluation_offline
from backend.evaluation.contracts import ArtifactIdentity
from backend.evaluation.loading import EVALUATION_ROOT, sha256_file
from backend.fine_tuning.scripts.adapter_loader import AdapterConfig


TOKENIZER_FILES = (
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "tokenizer.model",
)
ADAPTER_FILES = (
    "adapter_config.json",
    "adapter_model.safetensors",
)
PROTECTED_CODE_FILES = (
    Path("backend/app/models/chat_models.py"),
    Path("backend/app/services/ai_model_service.py"),
    Path("backend/app/services/api_context_service.py"),
    Path("backend/fine_tuning/scripts/adapter_loader.py"),
    Path("backend/fine_tuning/scripts/json_guardrail.py"),
)


class ArtifactIdentityError(RuntimeError):
    """Raised when an evaluation artifact cannot be identified locally."""


def _repository_root() -> Path:
    return EVALUATION_ROOT.parents[1]


def _hugging_face_hub_root() -> Path:
    if configured := os.getenv("HF_HUB_CACHE"):
        return Path(configured)
    if configured_home := os.getenv("HF_HOME"):
        return Path(configured_home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _resolve_model_snapshot(model_id: str) -> tuple[Path, str | None]:
    direct = Path(model_id)
    if direct.is_dir():
        return direct.resolve(), None

    repo_cache = _hugging_face_hub_root() / f"models--{model_id.replace('/', '--')}"
    ref_path = repo_cache / "refs" / "main"
    if not ref_path.is_file():
        raise ArtifactIdentityError("base_model_revision_not_available_in_local_cache")
    revision = ref_path.read_text(encoding="utf-8").strip()
    snapshot = repo_cache / "snapshots" / revision
    if not snapshot.is_dir():
        raise ArtifactIdentityError("base_model_snapshot_not_available_in_local_cache")
    return snapshot, revision


def _require_local_adapter(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ArtifactIdentityError("adapter_artifact_not_available_locally") from exc
    if not resolved.is_dir() or any(
        not (resolved / filename).is_file() for filename in ADAPTER_FILES
    ):
        raise ArtifactIdentityError("adapter_artifact_not_available_locally")
    return resolved


def _chat_template_hash(snapshot: Path) -> str | None:
    tokenizer_config_path = snapshot / "tokenizer_config.json"
    if not tokenizer_config_path.is_file():
        return None
    data = json.loads(tokenizer_config_path.read_text(encoding="utf-8"))
    template = data.get("chat_template")
    if not isinstance(template, str):
        return None
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


def _evaluation_code_version() -> str:
    root = _repository_root()
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "git-unavailable"

    files = sorted(EVALUATION_ROOT.glob("*.py"))
    files.extend(root / relative for relative in PROTECTED_CODE_FILES)
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"git:{commit};sha256:{digest.hexdigest()}"


def _resolve_adapter_path(config: AdapterConfig) -> Path:
    if config.local_adapter_path:
        return _require_local_adapter(Path(config.local_adapter_path))
    if config.adapter_repo_type == "model":
        repo_cache = (
            _hugging_face_hub_root()
            / f"models--{config.adapter_repo_id.replace('/', '--')}"
        )
        ref_path = repo_cache / "refs" / "main"
        if not ref_path.is_file():
            raise ArtifactIdentityError("adapter_artifact_not_available_locally")
        revision = ref_path.read_text(encoding="utf-8").strip()
        candidate = repo_cache / "snapshots" / revision
        if config.adapter_subfolder:
            candidate /= config.adapter_subfolder
        return _require_local_adapter(candidate)
    else:
        checkpoint_root = (
            _repository_root()
            / "backend"
            / "fine_tuning"
            / "checkpoints"
            / config.adapter_repo_id.replace("/", "__")
        )
        local_path = checkpoint_root / config.adapter_subfolder
        return _require_local_adapter(local_path)


def capture_artifact_identity(suite_sha256: str) -> ArtifactIdentity:
    enforce_evaluation_offline()
    from backend.evaluation.fixtures import blocked_external_network

    with blocked_external_network():
        return _capture_local_artifact_identity(suite_sha256)


def _capture_local_artifact_identity(suite_sha256: str) -> ArtifactIdentity:
    config = AdapterConfig.from_env()
    snapshot, revision = _resolve_model_snapshot(config.base_model_id)
    config_path = snapshot / "config.json"
    tokenizer_hashes = {
        filename: sha256_file(snapshot / filename)
        for filename in TOKENIZER_FILES
        if (snapshot / filename).is_file()
    }
    if not config_path.is_file() or not tokenizer_hashes:
        raise ArtifactIdentityError("base_model_identity_files_are_incomplete")

    adapter_path = _resolve_adapter_path(config)
    adapter_config_path = adapter_path / "adapter_config.json"
    adapter_weight_path = adapter_path / "adapter_model.safetensors"
    generation_configuration = {
        "model_temperature": config.model_temperature,
        "model_max_input_tokens": config.model_max_input_tokens,
        "model_max_new_tokens": config.model_max_new_tokens,
        "do_sample": config.model_temperature > 0,
        "top_p_when_sampling": 0.9,
        "use_cache": True,
        "complete_json_stopping_criteria": True,
        "generation_attempts_lane_a": 1,
        "application_generation_attempts_maximum": 2,
    }
    return ArtifactIdentity(
        base_model_id=config.base_model_id,
        base_revision=revision,
        base_config_sha256=sha256_file(config_path),
        tokenizer_file_sha256=tokenizer_hashes,
        chat_template_sha256=_chat_template_hash(snapshot),
        adapter_source=str(adapter_path),
        adapter_config_sha256=sha256_file(adapter_config_path),
        adapter_weight_sha256=sha256_file(adapter_weight_path),
        generation_configuration=generation_configuration,
        evaluation_suite_sha256=suite_sha256,
        evaluation_code_version=_evaluation_code_version(),
    )


def protected_artifact_hashes(identity: ArtifactIdentity) -> tuple[object, ...]:
    return (
        identity.base_model_id,
        identity.base_revision,
        identity.base_config_sha256,
        tuple(sorted(identity.tokenizer_file_sha256.items())),
        identity.chat_template_sha256,
        identity.adapter_config_sha256,
        identity.adapter_weight_sha256,
    )
