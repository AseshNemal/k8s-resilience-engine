"""Evaluate a saved method comparison against captured experiment evidence."""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.failure_impact_prediction.baseline_manifest import (  # noqa: E402
    parse_method_comparison_artifact,
)
from app.engine.failure_impact_prediction.method_evaluation import (  # noqa: E402
    FailureImpactMethodEvaluation,
    evaluate_failure_impact_methods,
)
from app.engine.failure_impact_prediction.method_evaluation_export import (  # noqa: E402
    build_method_evaluation_export,
    write_method_evaluation_artifact,
)
from app.engine.failure_impact_prediction.validation_evidence import (  # noqa: E402
    ObservedFailureEvidence,
    require_complete_observed_outcome,
)
from app.engine.failure_impact_prediction.validation_manifest import (  # noqa: E402
    parse_captured_experiment_manifest,
    require_complete_manifest_evidence,
)


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"Could not read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}: {error.msg}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


@dataclass(frozen=True)
class PreparedMethodEvaluation:
    """Validated in-memory evaluation and its source provenance."""

    evaluation: FailureImpactMethodEvaluation
    evidence: ObservedFailureEvidence
    comparison_generated_at: datetime


def prepare_saved_method_evaluation(
    *,
    comparison_payload: Mapping[str, object],
    evidence_payload: Mapping[str, object],
) -> PreparedMethodEvaluation:
    """Validate and evaluate one saved comparison/evidence pair."""

    parsed_comparison = parse_method_comparison_artifact(comparison_payload)
    manifest = parse_captured_experiment_manifest(evidence_payload)
    evidence = require_complete_manifest_evidence(manifest)
    observation = require_complete_observed_outcome(evidence)

    if evidence.deployment_snapshot_id is None:
        raise ValueError(
            "captured evidence must include deploymentSnapshotId for method evaluation"
        )
    if (
        evidence.deployment_snapshot_id
        != parsed_comparison.comparison.deployment_snapshot_id
    ):
        raise ValueError(
            "Method comparison and evidence must use the same deployment snapshot"
        )
    if parsed_comparison.generated_at >= evidence.observed_at:
        raise ValueError(
            "Method comparison must be generated before the observation"
        )
    if (
        parsed_comparison.comparison.workflow_id
        not in manifest.workflow_capture.expected_workflows
    ):
        raise ValueError(
            "Captured workflow scope must include the compared workflow"
        )

    evaluation = evaluate_failure_impact_methods(
        parsed_comparison.comparison,
        observation,
        observed_service_scope=manifest.service_capture.expected_services,
        observed_workflow_scope=manifest.workflow_capture.expected_workflows,
    )
    return PreparedMethodEvaluation(
        evaluation=evaluation,
        evidence=evidence,
        comparison_generated_at=parsed_comparison.generated_at,
    )


def evaluate_saved_method_artifacts(
    *,
    comparison_payload: Mapping[str, object],
    evidence_payload: Mapping[str, object],
    evaluated_at: datetime,
) -> dict[str, object]:
    """Combine one pre-failure comparison with complete post-failure evidence."""

    prepared = prepare_saved_method_evaluation(
        comparison_payload=comparison_payload,
        evidence_payload=evidence_payload,
    )
    return build_method_evaluation_export(
        prepared.evaluation,
        prepared.evidence,
        comparison_generated_at=prepared.comparison_generated_at,
        evaluated_at=evaluated_at,
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--comparison",
        type=Path,
        required=True,
        help="Path to a saved method-comparison.json artifact",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        required=True,
        help="Path to a complete captured experiment evidence manifest",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Write a new evaluation artifact without overwriting",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Evaluate, save, and print one evidence-backed method comparison."""

    arguments = _argument_parser().parse_args(argv)
    artifact = evaluate_saved_method_artifacts(
        comparison_payload=_read_json_object(arguments.comparison),
        evidence_payload=_read_json_object(arguments.evidence),
        evaluated_at=datetime.now(timezone.utc),
    )
    write_method_evaluation_artifact(arguments.output, artifact)
    print(json.dumps(artifact, indent=2))
    return 0


def cli(argv: Sequence[str] | None = None) -> int:
    """Return concise messages for expected input and evidence failures."""

    try:
        return main(argv)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
