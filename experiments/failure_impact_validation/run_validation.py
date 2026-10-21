"""Evaluate one or more saved predictions against captured evidence."""

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
    """Validate one in-memory pair through Component 3's existing pipeline."""

    return evaluate_saved_experiments(
        batch_id=batch_id,
        experiments=[(prediction_payload, evidence_payload)],
    )


def evaluate_saved_experiments(
    *,
    batch_id: str,
    experiments: Sequence[
        tuple[Mapping[str, object], Mapping[str, object]]
    ],
) -> dict[str, object]:
    """Aggregate complete saved experiments through the metric pipeline."""

    prepared: list[PredictionEvidenceExperiment] = []
    for prediction_payload, evidence_payload in experiments:
        prediction = FailureImpactReport.model_validate(prediction_payload)
        manifest = parse_captured_experiment_manifest(evidence_payload)
        evidence = require_complete_manifest_evidence(manifest)
        prepared.append(PredictionEvidenceExperiment(prediction, evidence))
    summary = evaluate_prediction_evidence(
        batch_id,
        prepared,
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


def _batch_manifest_inputs(
    path: Path,
) -> tuple[str, tuple[tuple[Path, Path], ...]]:
    payload = _read_json_object(path)
    expected_fields = {"batchId", "experiments"}
    unknown_fields = tuple(sorted(set(payload) - expected_fields))
    missing_fields = tuple(sorted(expected_fields - set(payload)))
    if missing_fields:
        raise ValueError(
            "batch manifest is missing fields: " + ", ".join(missing_fields)
        )
    if unknown_fields:
        raise ValueError(
            "batch manifest contains unknown fields: "
            + ", ".join(unknown_fields)
        )

    batch_id = payload["batchId"]
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ValueError("batch manifest batchId must be a non-empty string")
    entries = payload["experiments"]
    if not isinstance(entries, list) or not entries:
        raise ValueError(
            "batch manifest experiments must be a non-empty array"
        )

    pairs: list[tuple[Path, Path]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ValueError(
                f"batch manifest experiment {index} must be an object"
            )
        entry_fields = set(entry)
        required_entry_fields = {"prediction", "evidence"}
        missing_entry_fields = tuple(
            sorted(required_entry_fields - entry_fields)
        )
        unknown_entry_fields = tuple(
            sorted(entry_fields - required_entry_fields)
        )
        if missing_entry_fields:
            raise ValueError(
                f"batch manifest experiment {index} is missing fields: "
                + ", ".join(missing_entry_fields)
            )
        if unknown_entry_fields:
            raise ValueError(
                f"batch manifest experiment {index} contains unknown fields: "
                + ", ".join(unknown_entry_fields)
            )

        resolved_paths: list[Path] = []
        for field_name in ("prediction", "evidence"):
            value = entry[field_name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"batch manifest experiment {index} {field_name} "
                    "must be a non-empty path string"
                )
            artifact_path = Path(value)
            if not artifact_path.is_absolute():
                artifact_path = path.parent / artifact_path
            resolved_paths.append(artifact_path)
        pairs.append((resolved_paths[0], resolved_paths[1]))
    return batch_id, tuple(pairs)


def write_validation_artifact(
    path: Path,
    artifact: Mapping[str, object],
) -> Path:
    """Write one new UTF-8 artifact without replacing prior evidence."""

    created = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            json.dump(artifact, stream, indent=2)
            stream.write("\n")
    except FileExistsError as error:
        raise ValueError(
            f"refusing to overwrite existing validation artifact: {path}"
        ) from error
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
    return path


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prediction",
        type=Path,
        help="Path to a saved FailureImpactReport JSON object",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        help="Path to a captured experiment manifest JSON object",
    )
    parser.add_argument(
        "--batch-id",
        help="Traceable identifier for the validation batch",
    )
    parser.add_argument(
        "--batch-manifest",
        type=Path,
        help=(
            "JSON file containing batchId and prediction/evidence paths for "
            "repeated experiments"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the validation JSON to a new file without overwriting",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Read saved artifacts and print validation JSON without writing files."""

    parser = _argument_parser()
    arguments = parser.parse_args(argv)
    single_arguments = (
        arguments.prediction,
        arguments.evidence,
        arguments.batch_id,
    )
    if arguments.batch_manifest is not None:
        if any(value is not None for value in single_arguments):
            parser.error(
                "--batch-manifest cannot be combined with --prediction, "
                "--evidence, or --batch-id"
            )
        batch_id, artifact_paths = _batch_manifest_inputs(
            arguments.batch_manifest
        )
        artifact = evaluate_saved_experiments(
            batch_id=batch_id,
            experiments=[
                (_read_json_object(prediction), _read_json_object(evidence))
                for prediction, evidence in artifact_paths
            ],
        )
    else:
        missing = tuple(
            name
            for name, value in (
                ("--prediction", arguments.prediction),
                ("--evidence", arguments.evidence),
                ("--batch-id", arguments.batch_id),
            )
            if value is None
        )
        if missing:
            parser.error(
                "single-experiment validation requires " + ", ".join(missing)
            )
        artifact = evaluate_saved_experiment(
            batch_id=arguments.batch_id,
            prediction_payload=_read_json_object(arguments.prediction),
            evidence_payload=_read_json_object(arguments.evidence),
        )
    if arguments.output is not None:
        write_validation_artifact(arguments.output, artifact)
    print(json.dumps(artifact, indent=2))
    return 0


def cli(argv: Sequence[str] | None = None) -> int:
    """Return a concise command-line error for expected input failures."""

    try:
        return main(argv)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
