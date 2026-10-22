"""Aggregate repeated comparison/evidence pairs into method-level metrics."""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.failure_impact_prediction.method_evaluation_batch import (  # noqa: E402
    build_method_evaluation_batch,
)
from app.engine.failure_impact_prediction.method_evaluation_batch_export import (  # noqa: E402
    build_method_evaluation_batch_export,
    write_method_evaluation_batch_artifact,
)
from experiments.failure_impact_validation.run_method_evaluation import (  # noqa: E402
    prepare_saved_method_evaluation,
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


def _batch_manifest_inputs(
    path: Path,
) -> tuple[str, tuple[tuple[Path, Path], ...]]:
    payload = _read_json_object(path)
    expected_fields = {"batchId", "experiments"}
    missing = tuple(sorted(expected_fields - set(payload)))
    unknown = tuple(sorted(set(payload) - expected_fields))
    if missing:
        raise ValueError(
            "method batch manifest is missing fields: " + ", ".join(missing)
        )
    if unknown:
        raise ValueError(
            "method batch manifest contains unknown fields: "
            + ", ".join(unknown)
        )

    batch_id = payload["batchId"]
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ValueError("method batch manifest batchId must be a non-empty string")
    entries = payload["experiments"]
    if not isinstance(entries, list) or not entries:
        raise ValueError(
            "method batch manifest experiments must be a non-empty array"
        )

    pairs: list[tuple[Path, Path]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ValueError(
                f"method batch manifest experiment {index} must be an object"
            )
        required = {"comparison", "evidence"}
        missing_entry = tuple(sorted(required - set(entry)))
        unknown_entry = tuple(sorted(set(entry) - required))
        if missing_entry:
            raise ValueError(
                f"method batch manifest experiment {index} is missing fields: "
                + ", ".join(missing_entry)
            )
        if unknown_entry:
            raise ValueError(
                f"method batch manifest experiment {index} contains unknown fields: "
                + ", ".join(unknown_entry)
            )

        resolved: list[Path] = []
        for field_name in ("comparison", "evidence"):
            value = entry[field_name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"method batch manifest experiment {index} {field_name} "
                    "must be a non-empty path string"
                )
            artifact_path = Path(value)
            if not artifact_path.is_absolute():
                artifact_path = path.parent / artifact_path
            resolved.append(artifact_path)
        pairs.append((resolved[0], resolved[1]))
    return batch_id.strip(), tuple(pairs)


def evaluate_saved_method_batch(
    *,
    batch_id: str,
    experiments: Sequence[
        tuple[Mapping[str, object], Mapping[str, object]]
    ],
    generated_at: datetime,
) -> dict[str, object]:
    """Revalidate and aggregate repeated saved experiment inputs."""

    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must include timezone information")
    prepared = tuple(
        prepare_saved_method_evaluation(
            comparison_payload=comparison,
            evidence_payload=evidence,
        )
        for comparison, evidence in experiments
    )
    if prepared and generated_at < max(
        item.evidence.observed_at for item in prepared
    ):
        raise ValueError(
            "generated_at cannot be earlier than an experiment observation"
        )
    batch = build_method_evaluation_batch(
        batch_id,
        [item.evaluation for item in prepared],
    )
    return build_method_evaluation_batch_export(
        batch,
        generated_at=generated_at,
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch-manifest",
        type=Path,
        required=True,
        help="JSON manifest containing comparison/evidence path pairs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Write a new aggregate artifact without overwriting",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Evaluate all manifest entries, save, and print aggregate metrics."""

    arguments = _argument_parser().parse_args(argv)
    batch_id, paths = _batch_manifest_inputs(arguments.batch_manifest)
    artifact = evaluate_saved_method_batch(
        batch_id=batch_id,
        experiments=[
            (_read_json_object(comparison), _read_json_object(evidence))
            for comparison, evidence in paths
        ],
        generated_at=datetime.now(timezone.utc),
    )
    write_method_evaluation_batch_artifact(arguments.output, artifact)
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
