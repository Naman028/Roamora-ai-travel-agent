from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationLane(StrEnum):
    MODEL = "model"
    APPLICATION = "application"


class ModelVariant(StrEnum):
    BASE = "base"
    FINE_TUNED = "fine_tuned"


class SuiteSplit(StrEnum):
    DEVELOPMENT = "development"
    FINAL = "final"


class ProviderName(StrEnum):
    WEATHER = "weather"
    FLIGHTS = "flights"
    HOTELS = "hotels"
    EVENTS = "events"


class ProviderState(StrEnum):
    SUCCESS = "success"
    EMPTY = "empty"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"


class RunStatus(StrEnum):
    COMPLETED = "completed"
    EXECUTION_FAILED = "execution_failed"


class HardFailureCode(StrEnum):
    FABRICATED_FLIGHT = "fabricated_authoritative_flight"
    FABRICATED_HOTEL = "fabricated_authoritative_hotel_availability"
    FABRICATED_PRICE = "fabricated_authoritative_price"
    FABRICATED_ROUTE = "fabricated_routing_distance_or_duration"
    FABRICATED_COORDINATES = "fabricated_coordinates"
    INVALID_GROUNDING_ID = "invalid_grounding_id"
    WRONG_SELECTED_MODEL = "wrong_selected_model"
    SILENT_MODEL_FALLBACK = "silent_model_fallback"
    PUBLIC_SCHEMA_FAILURE = "public_chat_response_schema_failure"


LanguageCode = Literal["en", "de", "fr", "it", "pt", "hi", "es", "th"]


class TravelInfoFixture(StrictModel):
    origin: str | None = None
    destination: str | None = None
    departure_date: str = "2030-04-10"
    return_date: str = "2030-04-13"
    check_in: str = "2030-04-10"
    check_out: str = "2030-04-13"
    duration_days: int = Field(default=3, ge=1, le=10)
    guests: int = Field(default=2, ge=1, le=20)
    budget: str | int | float | None = None


class TurnExpectation(StrictModel):
    intent: str
    expected_provider_calls: list[ProviderName] = Field(default_factory=list)
    expected_selected_ids: list[str] = Field(default_factory=list)
    allowed_grounding_ids: list[str] = Field(default_factory=list)
    language_markers: list[str] = Field(default_factory=list)
    must_include_terms: list[str] = Field(default_factory=list)
    must_not_include_terms: list[str] = Field(default_factory=list)
    requires_clarification: bool = False


class EvaluationTurn(StrictModel):
    message: str = Field(min_length=1, max_length=4000)
    provider_fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    travel_info: TravelInfoFixture
    expectation: TurnExpectation


class EvaluationCase(StrictModel):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,95}$")
    scenario_family: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,95}$")
    split: SuiteSplit
    language: LanguageCode
    linguistic_review_required: bool
    skills: list[str] = Field(min_length=1)
    notes: str = ""
    turns: list[EvaluationTurn] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def require_human_review_for_non_english(self) -> "EvaluationCase":
        if self.language != "en" and not self.linguistic_review_required:
            raise ValueError("non_english_cases_require_linguistic_review")
        if len(self.skills) != len(set(self.skills)):
            raise ValueError("case_skills_must_be_unique")
        return self


class EvaluationSuite(StrictModel):
    schema_version: Literal["roamora_evaluation_suite_v1"]
    suite_version: str = Field(pattern=r"^v[1-9][0-9]*$")
    split: SuiteSplit
    sealed: bool
    review_status: Literal["pending_human_review", "human_reviewed"]
    authored_utc: datetime
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_identity(self) -> "EvaluationSuite":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("suite_case_ids_must_be_unique")
        if any(case.split != self.split for case in self.cases):
            raise ValueError("case_split_must_match_suite_split")
        if self.split == SuiteSplit.FINAL and not self.sealed:
            raise ValueError("final_suite_must_be_sealed")
        return self


class ProviderFixture(StrictModel):
    fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    states: dict[ProviderName, ProviderState]
    results: dict[ProviderName, JsonValue]
    authoritative_ids: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def require_every_provider(self) -> "ProviderFixture":
        expected = set(ProviderName)
        if set(self.states) != expected or set(self.results) != expected:
            raise ValueError("fixture_must_define_every_provider")
        if len(self.authoritative_ids) != len(set(self.authoritative_ids)):
            raise ValueError("authoritative_ids_must_be_unique")
        return self


class ProviderFixtureRegistry(StrictModel):
    schema_version: Literal["roamora_provider_fixtures_v1"]
    fixtures: list[ProviderFixture]

    @model_validator(mode="after")
    def require_unique_fixture_ids(self) -> "ProviderFixtureRegistry":
        fixture_ids = [fixture.fixture_id for fixture in self.fixtures]
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError("provider_fixture_ids_must_be_unique")
        return self


class ApplicationStateFixture(StrictModel):
    fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    session_id_prefix: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$")
    max_new_tokens: int | None = Field(default=None, ge=384, le=1800)
    fixed_utc: datetime


class ApplicationStateRegistry(StrictModel):
    schema_version: Literal["roamora_application_state_v1"]
    fixtures: list[ApplicationStateFixture]


class SealedFile(StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class EvaluationSeal(StrictModel):
    schema_version: Literal["roamora_evaluation_seal_v1"]
    suite_version: str
    files: list[SealedFile]
    combined_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class FinalHoldoutMetadata(StrictModel):
    schema_version: Literal["roamora_final_holdout_metadata_v1"]
    suite_version: str = Field(pattern=r"^v[1-9][0-9]*$")
    split: Literal[SuiteSplit.FINAL]
    sealed: Literal[True]
    review_status: Literal["pending_human_review", "human_reviewed"]
    authored_utc: datetime
    logical_seal_path: Literal["suites/v1/final.json"]
    suite_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    case_count: int = Field(ge=1)
    turn_count: int = Field(ge=1)
    storage: Literal["external_private_path_required"]
    path_environment_variable: Literal["ROAMORA_FINAL_EVALUATION_SUITE_PATH"]


class TimingMetrics(StrictModel):
    cuda_initialization_seconds: float | None = None
    model_load_seconds: float | None = None
    adapter_attachment_seconds: float | None = None
    generation_seconds: float | None = None
    parse_normalization_seconds: float | None = None
    application_total_seconds: float | None = None
    ttft_seconds: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    tokens_per_second: float | None = None
    peak_vram_bytes: int | None = None


class ScoreMetrics(StrictModel):
    strict_json_valid: bool
    expected_structure_valid: bool
    public_schema_valid: bool | None
    intent_match: bool
    required_terms_present: bool
    forbidden_terms_absent: bool
    grounding_ids_valid: bool
    selected_ids_match: bool | None
    language_heuristic_match: bool | None
    linguistic_review_required: bool
    provider_calls_match: bool | None


class TurnResult(StrictModel):
    case_id: str
    turn_index: int = Field(ge=1, le=4)
    lane: EvaluationLane
    model_variant: ModelVariant
    status: RunStatus
    prompt_sha256: str | None = None
    raw_output_files: list[str] = Field(default_factory=list)
    generation_attempts: int = Field(default=0, ge=0, le=2)
    loaded_variants: list[ModelVariant] = Field(default_factory=list)
    provider_call_counts: dict[ProviderName, int] = Field(default_factory=dict)
    retry_used: bool | None = None
    fallback_used: bool | None = None
    selected_model: ModelVariant | None = None
    adapter_loaded: bool | None = None
    scores: ScoreMetrics
    hard_failures: list[HardFailureCode] = Field(default_factory=list)
    timings: TimingMetrics
    public_response: JsonValue = None
    observations: dict[str, JsonValue] = Field(default_factory=dict)
    sanitized_error_code: str | None = None


class ArtifactIdentity(StrictModel):
    base_model_id: str
    base_revision: str | None
    base_config_sha256: str | None
    tokenizer_file_sha256: dict[str, str]
    chat_template_sha256: str | None
    adapter_source: str
    adapter_config_sha256: str
    adapter_weight_sha256: str
    generation_configuration: dict[str, JsonValue]
    evaluation_suite_sha256: str
    evaluation_code_version: str


class EvaluationRunReport(StrictModel):
    schema_version: Literal["roamora_evaluation_report_v1"]
    run_id: str
    started_utc: datetime
    completed_utc: datetime
    suite_version: str
    suite_split: SuiteSplit
    suite_sha256: str
    artifact_identity: ArtifactIdentity | None
    environment: dict[str, JsonValue]
    results: list[TurnResult]
    aggregate: dict[str, JsonValue]
    limitations: list[str]
    source_run_id: str | None = None
    source_evaluation_code_version: str | None = None
