"""Evaluate a saved failure-impact prediction against captured evidence."""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.failure_impact_prediction.validation_export import (  # noqa: E402
    build_validation_summary_export,
)
from app.engine.failure_impact_prediction.validation_manifest import (  # noqa: E402
    parse_captured_experiment_manifest,
    require_complete_manifest_evidence,
)
from app.engine.failure_impact_prediction.validation_pipeline import (  # noqa: E402
    PredictionEvidenceExperiment,
    evaluate_prediction_evidence,
)
from app.shared.schemas.failure_impact import FailureImpactReport  # noqa: E402


ARTIFACT_TYPE = "FAILURE_IMPACT_VALIDATION"
EVIDENCE_STATUS = "CAPTURED_EXPERIMENT"


def evaluate_saved_experiment(
    *,
    batch_id: str,
    prediction_payload: Mapping[str, object],
    evidence_payload: Mapping[str, object],
) -> dict[str, object]:
    """Validate in-memory payloads through Component 3's existing pipeline."""

    prediction = FailureImpactReport.model_validate(prediction_payload)
    manifest = parse_captured_experiment_manifest(evidence_payload)
    evidence = require_complete_manifest_evidence(manifest)
    summary = evaluate_prediction_evidence(
        batch_id,
        [PredictionEvidenceExperiment(prediction, evidence)],
    )
    return {
        "artifactType": ARTIFACT_TYPE,
        "evidenceStatus": EVIDENCE_STATUS,
        "validation": build_validation_summary_export(summary),
    }


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


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prediction",
        required=True,
        type=Path,
        help="Path to a saved FailureImpactReport JSON object",
    )
    parser.add_argument(
        "--evidence",
        required=True,
        type=Path,
        help="Path to a captured experiment manifest JSON object",
    )
    parser.add_argument(
        "--batch-id",
        required=True,
        help="Traceable identifier for the validation batch",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Read two artifacts and print validation JSON without writing files."""

    arguments = _argument_parser().parse_args(argv)
    artifact = evaluate_saved_experiment(
        batch_id=arguments.batch_id,
        prediction_payload=_read_json_object(arguments.prediction),
        evidence_payload=_read_json_object(arguments.evidence),
    )
    print(json.dumps(artifact, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
