"""Run Component 3 against one or all explicitly disposable node scenarios."""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.failure_impact_prediction.service import (  # noqa: E402
    predict_failure_impact,
)
from app.shared.schemas.deployment import DeploymentGraph  # noqa: E402
from app.shared.schemas.workflow import WorkflowGraph  # noqa: E402
from experiments.failure_impact_validation.generate_disposable_inputs import (  # noqa: E402
    ARTIFACT_TYPE as INPUT_ARTIFACT_TYPE,
    DISCLAIMER,
    EVIDENCE_STATUS,
)


ARTIFACT_TYPE = "DISPOSABLE_COMPONENT3_SCENARIO_RESULTS"
WORKFLOW_FILE = "workflow-graph.json"
DEPLOYMENT_FILE = "deployment-graph.json"
METADATA_FILE = "synthetic-input-metadata.json"


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("generated_at must include timezone information")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _require_disposable_metadata(
    metadata: Mapping[str, object],
) -> str:
    if metadata.get("artifactType") != INPUT_ARTIFACT_TYPE:
        raise ValueError(
            "input metadata is not a disposable Component 3 input artifact"
        )
    if metadata.get("evidenceStatus") != EVIDENCE_STATUS:
        raise ValueError(
            "input metadata must be labelled SYNTHETIC_DISPOSABLE"
        )
    if metadata.get("disclaimer") != DISCLAIMER:
        raise ValueError("input metadata is missing the synthetic disclaimer")
    scenario_id = metadata.get("scenarioId")
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        raise ValueError("input metadata scenarioId must be a non-empty string")
    if metadata.get("workflowFile") != WORKFLOW_FILE:
        raise ValueError(f"input metadata workflowFile must be {WORKFLOW_FILE}")
    if metadata.get("deploymentFile") != DEPLOYMENT_FILE:
        raise ValueError(
            f"input metadata deploymentFile must be {DEPLOYMENT_FILE}"
        )
    return scenario_id


def _selected_nodes(
    deployment: DeploymentGraph,
    requested_nodes: Sequence[str],
) -> tuple[str, ...]:
    available = tuple(sorted(node.name for node in deployment.nodes))
    if not available:
        raise ValueError("the disposable deployment contains no nodes")

    normalized_values: set[str] = set()
    for node in requested_nodes:
        normalized = node.strip()
        if not normalized:
            raise ValueError("failed nodes must be non-empty strings")
        normalized_values.add(normalized)
    normalized = tuple(sorted(normalized_values))
    selected = normalized or available
    unknown = tuple(sorted(set(selected) - set(available)))
    if unknown:
        raise ValueError("unknown failed nodes: " + ", ".join(unknown))
    return selected


def evaluate_disposable_scenarios(
    *,
    metadata: Mapping[str, object],
    workflow_payload: Mapping[str, object],
    deployment_payload: Mapping[str, object],
    failed_nodes: Sequence[str],
    generated_at: datetime,
) -> dict[str, object]:
    """Evaluate selected synthetic node failures through the public service."""

    scenario_id = _require_disposable_metadata(metadata)
    workflow = WorkflowGraph.model_validate(workflow_payload)
    deployment = DeploymentGraph.model_validate(deployment_payload)
    expected_workflow_id = f"synthetic-{scenario_id}-checkout"
    expected_snapshot_id = f"synthetic-{scenario_id}-deployment"
    if workflow.workflow_id != expected_workflow_id:
        raise ValueError(
            "workflow graph does not match the disposable scenario metadata"
        )
    if deployment.snapshot_id != expected_snapshot_id:
        raise ValueError(
            "deployment graph does not match the disposable scenario metadata"
        )
    selected = _selected_nodes(deployment, failed_nodes)
    results = [
        predict_failure_impact(
            workflow,
            deployment,
            failed_node,
        ).model_dump(by_alias=True, mode="json")
        for failed_node in selected
    ]
    return {
        "artifactType": ARTIFACT_TYPE,
        "evidenceStatus": EVIDENCE_STATUS,
        "disclaimer": DISCLAIMER,
        "scenarioId": scenario_id,
        "generatedAt": _timestamp(generated_at),
        "workflowId": workflow.workflow_id,
        "deploymentSnapshotId": deployment.snapshot_id,
        "evaluatedNodes": list(selected),
        "results": results,
    }


def run_disposable_scenarios(
    *,
    input_dir: Path,
    failed_nodes: Sequence[str],
    output: Path,
    generated_at: datetime,
) -> dict[str, object]:
    """Read one generated input set and write a new scenario result."""

    artifact = evaluate_disposable_scenarios(
        metadata=_read_json_object(input_dir / METADATA_FILE),
        workflow_payload=_read_json_object(input_dir / WORKFLOW_FILE),
        deployment_payload=_read_json_object(input_dir / DEPLOYMENT_FILE),
        failed_nodes=failed_nodes,
        generated_at=generated_at,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(artifact, stream, indent=2)
            stream.write("\n")
    except FileExistsError as error:
        raise ValueError(
            f"refusing to overwrite existing scenario result: {output}"
        ) from error
    return artifact


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        required=True,
        type=Path,
        help="Directory created by generate_disposable_inputs.py",
    )
    parser.add_argument(
        "--failed-node",
        action="append",
        default=[],
        help="Node to fail; repeat for several nodes or omit to evaluate all nodes",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Write a new synthetic scenario result without overwriting",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Evaluate the requested disposable scenarios and print the artifact."""

    arguments = _argument_parser().parse_args(argv)
    artifact = run_disposable_scenarios(
        input_dir=arguments.input_dir,
        failed_nodes=arguments.failed_node,
        output=arguments.output,
        generated_at=datetime.now(timezone.utc),
    )
    print(json.dumps(artifact, indent=2))
    return 0


def cli(argv: Sequence[str] | None = None) -> int:
    """Return concise messages for expected scenario-runner failures."""

    try:
        return main(argv)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
