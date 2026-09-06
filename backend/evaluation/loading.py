from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from backend.evaluation.contracts import (
    ApplicationStateRegistry,
    EvaluationSeal,
    EvaluationSuite,
    FinalHoldoutMetadata,
    ProviderFixture,
    ProviderFixtureRegistry,
    SealedFile,
)


EVALUATION_ROOT = Path(__file__).resolve().parent
DATA_ROOT = EVALUATION_ROOT / "data"
SUITE_ROOT = EVALUATION_ROOT / "suites" / "v1"
REPOSITORY_ROOT = EVALUATION_ROOT.parents[1]
FINAL_SUITE_ENVIRONMENT_VARIABLE = "ROAMORA_FINAL_EVALUATION_SUITE_PATH"
FINAL_SUITE_LOGICAL_PATH = Path("suites/v1/final.json")
FINAL_METADATA_PATH = SUITE_ROOT / "final.metadata.json"
TRACKED_SEALED_PATHS = (
    Path("data/provider_fixtures.json"),
    Path("data/application_states.json"),
    Path("suites/v1/development.json"),
)


class EvaluationIntegrityError(RuntimeError):
    """Raised when sealed evaluation inputs do not match their manifest."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def combined_sha256(files: list[SealedFile]) -> str:
    canonical = "\n".join(f"{item.path}:{item.sha256}" for item in files)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def resolve_final_suite_path() -> Path:
    configured = os.environ.get(FINAL_SUITE_ENVIRONMENT_VARIABLE)
    if not configured:
        raise EvaluationIntegrityError("final_suite_private_path_not_configured")
    configured_path = Path(configured).expanduser()
    if not configured_path.is_absolute():
        raise EvaluationIntegrityError("final_suite_private_path_must_be_absolute")
    try:
        path = configured_path.resolve(strict=True)
    except OSError as exc:
        raise EvaluationIntegrityError("final_suite_private_path_unavailable") from exc
    if not path.is_file():
        raise EvaluationIntegrityError("final_suite_private_path_is_not_a_file")
    if _is_within(path, REPOSITORY_ROOT.resolve()):
        raise EvaluationIntegrityError("final_suite_must_be_stored_outside_repository")
    return path


def load_final_holdout_metadata() -> FinalHoldoutMetadata:
    return FinalHoldoutMetadata.model_validate_json(
        FINAL_METADATA_PATH.read_text(encoding="utf-8")
    )


def load_suite(split: str) -> EvaluationSuite:
    if split == "development":
        path = SUITE_ROOT / "development.json"
    elif split == "final":
        path = resolve_final_suite_path()
    else:
        raise ValueError("unsupported_evaluation_split")
    return EvaluationSuite.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def load_provider_fixtures() -> dict[str, ProviderFixture]:
    registry = ProviderFixtureRegistry.model_validate_json(
        (DATA_ROOT / "provider_fixtures.json").read_text(encoding="utf-8")
    )
    return {fixture.fixture_id: fixture for fixture in registry.fixtures}


def load_application_states() -> ApplicationStateRegistry:
    return ApplicationStateRegistry.model_validate_json(
        (DATA_ROOT / "application_states.json").read_text(encoding="utf-8")
    )


def build_seal() -> EvaluationSeal:
    files = [
        SealedFile(path=path.as_posix(), sha256=sha256_file(EVALUATION_ROOT / path))
        for path in TRACKED_SEALED_PATHS
    ]
    files.append(
        SealedFile(
            path=FINAL_SUITE_LOGICAL_PATH.as_posix(),
            sha256=sha256_file(resolve_final_suite_path()),
        )
    )
    return EvaluationSeal(
        schema_version="roamora_evaluation_seal_v1",
        suite_version="v1",
        files=files,
        combined_sha256=combined_sha256(files),
    )


def load_seal() -> EvaluationSeal:
    return EvaluationSeal.model_validate_json(
        (SUITE_ROOT / "seal.json").read_text(encoding="utf-8")
    )


def _expected_sealed_file(seal: EvaluationSeal, path: Path) -> SealedFile:
    matches = [item for item in seal.files if item.path == path.as_posix()]
    if len(matches) != 1:
        raise EvaluationIntegrityError("sealed_file_manifest_entry_invalid")
    return matches[0]


def _validate_tracked_inputs() -> tuple[EvaluationSeal, FinalHoldoutMetadata]:
    expected = load_seal()
    metadata = load_final_holdout_metadata()
    tracked_files = [
        SealedFile(path=path.as_posix(), sha256=sha256_file(EVALUATION_ROOT / path))
        for path in TRACKED_SEALED_PATHS
    ]
    for actual in tracked_files:
        if _expected_sealed_file(expected, Path(actual.path)) != actual:
            raise EvaluationIntegrityError("sealed_evaluation_inputs_do_not_match_manifest")

    expected_final = _expected_sealed_file(expected, FINAL_SUITE_LOGICAL_PATH)
    if metadata.suite_version != expected.suite_version:
        raise EvaluationIntegrityError("final_suite_metadata_version_mismatch")
    if metadata.logical_seal_path != expected_final.path:
        raise EvaluationIntegrityError("final_suite_metadata_path_mismatch")
    if metadata.suite_sha256 != expected_final.sha256:
        raise EvaluationIntegrityError("final_suite_metadata_hash_mismatch")

    manifest_files = [*tracked_files, expected_final]
    if (
        expected.files != manifest_files
        or expected.combined_sha256 != combined_sha256(manifest_files)
    ):
        raise EvaluationIntegrityError("sealed_evaluation_inputs_do_not_match_manifest")
    return expected, metadata


def validate_sealed_inputs(split: str = "development") -> EvaluationSeal:
    expected, metadata = _validate_tracked_inputs()
    development = load_suite("development")
    fixture_ids = set(load_provider_fixtures())
    development_ids = {
        turn.provider_fixture_id
        for case in development.cases
        for turn in case.turns
    }
    if missing := development_ids - fixture_ids:
        raise EvaluationIntegrityError(
            f"unknown_provider_fixture_ids:{','.join(sorted(missing))}"
        )
    load_application_states()

    if split == "development":
        return expected
    if split != "final":
        raise ValueError("unsupported_evaluation_split")

    actual = build_seal()
    if expected != actual:
        raise EvaluationIntegrityError("sealed_evaluation_inputs_do_not_match_manifest")
    final = load_suite("final")
    if (
        final.suite_version != metadata.suite_version
        or final.split.value != metadata.split
        or final.sealed != metadata.sealed
        or final.review_status != metadata.review_status
        or final.authored_utc != metadata.authored_utc
        or len(final.cases) != metadata.case_count
        or sum(len(case.turns) for case in final.cases) != metadata.turn_count
    ):
        raise EvaluationIntegrityError("final_suite_metadata_content_mismatch")
    development_families = {case.scenario_family for case in development.cases}
    final_families = {case.scenario_family for case in final.cases}
    if development_families & final_families:
        raise EvaluationIntegrityError("scenario_family_leakage_between_splits")
    final_ids = {
        turn.provider_fixture_id
        for case in final.cases
        for turn in case.turns
    }
    if missing := final_ids - fixture_ids:
        raise EvaluationIntegrityError(
            f"unknown_provider_fixture_ids:{','.join(sorted(missing))}"
        )
    return expected


def serialize_seal(seal: EvaluationSeal) -> str:
    data = seal.model_dump(mode="json")
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
