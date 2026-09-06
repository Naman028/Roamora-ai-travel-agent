from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from backend.evaluation import enforce_evaluation_offline
from backend.evaluation.contracts import EvaluationLane, ModelVariant
from backend.evaluation.identity import capture_artifact_identity
from backend.evaluation.loading import (
    load_final_holdout_metadata,
    load_provider_fixtures,
    load_suite,
    validate_sealed_inputs,
)
from backend.evaluation.reporting import coverage_summary, environment_identity
from backend.evaluation.runner import execute_evaluation, rescore_evaluation


DEFAULT_OUTPUT_ROOT = (
    Path(__file__).resolve().parents[1] / "fine_tuning" / "outputs" / "evaluation"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.evaluation",
        description="Offline Base/V1 evaluation for Roamora.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate tracked inputs without opening the private final suite.",
    )
    validate_parser.add_argument(
        "--include-final",
        action="store_true",
        help="Also validate the explicitly configured private final holdout.",
    )
    subparsers.add_parser("identity", help="Print local model and environment identity.")

    rescore_parser = subparsers.add_parser(
        "rescore",
        help="Re-score a completed report without generating again.",
    )
    rescore_parser.add_argument("--report", type=Path, required=True)
    rescore_parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)

    run_parser = subparsers.add_parser("run", help="Run a bounded sealed evaluation.")
    run_parser.add_argument(
        "--split",
        choices=("development", "final"),
        required=True,
    )
    run_parser.add_argument(
        "--models",
        nargs="+",
        choices=tuple(variant.value for variant in ModelVariant),
        default=[variant.value for variant in ModelVariant],
    )
    run_parser.add_argument(
        "--lanes",
        nargs="+",
        choices=tuple(lane.value for lane in EvaluationLane),
        default=[lane.value for lane in EvaluationLane],
    )
    run_parser.add_argument("--case-limit", type=int)
    run_parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def _validate_case_limit(value: int | None) -> None:
    if value is not None and value < 1:
        raise SystemExit("--case-limit must be at least 1")


def main(argv: list[str] | None = None) -> int:
    # Reassert the evaluator policy in case an embedding process changed the
    # environment after importing this package.
    enforce_evaluation_offline()
    args = _parser().parse_args(argv)
    if args.command == "validate":
        seal = validate_sealed_inputs(
            "final" if args.include_final else "development"
        )
        fixtures = load_provider_fixtures()
        final_metadata = load_final_holdout_metadata()
        result = {
            "suite_version": seal.suite_version,
            "combined_sha256": seal.combined_sha256,
            "development": coverage_summary(load_suite("development"), fixtures),
            "final_holdout": final_metadata.model_dump(mode="json"),
            "final_content_validated": args.include_final,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "identity":
        seal = validate_sealed_inputs("development")
        result = {
            "artifact_identity": capture_artifact_identity(
                seal.combined_sha256
            ).model_dump(mode="json"),
            "environment": environment_identity(),
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "rescore":
        report, json_path, markdown_path = rescore_evaluation(
            source_report_path=args.report,
            output_root=args.output_root,
        )
        print(
            json.dumps(
                {
                    "run_id": report.run_id,
                    "source_run_id": report.source_run_id,
                    "report_json": str(json_path.resolve()),
                    "report_markdown": str(markdown_path.resolve()),
                    "aggregate": report.aggregate,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    _validate_case_limit(args.case_limit)
    report, json_path, markdown_path = asyncio.run(
        execute_evaluation(
            split=args.split,
            variants=[ModelVariant(value) for value in args.models],
            lanes=[EvaluationLane(value) for value in args.lanes],
            output_root=args.output_root,
            case_limit=args.case_limit,
        )
    )
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "report_json": str(json_path.resolve()),
                "report_markdown": str(markdown_path.resolve()),
                "aggregate": report.aggregate,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0
