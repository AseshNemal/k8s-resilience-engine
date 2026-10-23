"""Generate explicitly synthetic Component 3 inputs for demonstrations."""

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.shared.schemas.deployment import (  # noqa: E402
    ClusterMetadata,
    DeploymentGraph,
    NodeModel,
    PodModel,
    ServicePlacement,
)
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph  # noqa: E402


ARTIFACT_TYPE = "DISPOSABLE_COMPONENT3_INPUTS"
EVIDENCE_STATUS = "SYNTHETIC_DISPOSABLE"
DISCLAIMER = (
    "Synthetic disposable inputs for Component 3 demonstration only. "
    "They are not observed Kubernetes evidence and must not be reported as "
    "experimental validation results."
)
SCENARIO_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _normalized_scenario_id(value: str) -> str:
    scenario_id = value.strip()
    if not SCENARIO_ID_PATTERN.fullmatch(scenario_id):
        raise ValueError(
            "scenario_id must use 1-64 letters, numbers, dots, underscores, "
            "or hyphens and must start with a letter or number"
        )
    return scenario_id


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("generated_at must include timezone information")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _pod(name: str, node: str, zone: str) -> PodModel:
    return PodModel(
        name=name,
        node=node,
        zone=zone,
        ready=True,
        phase="Running",
        restart_count=0,
        namespace="synthetic-viva",
        owner_kind="Deployment",
        owner_name=name.rsplit("-", 1)[0],
    )


def _placement(
    name: str,
    pods: list[PodModel],
) -> ServicePlacement:
    hosting_nodes = sorted({pod.node for pod in pods})
    counts = {
        node: sum(pod.node == node for pod in pods)
        for node in hosting_nodes
    }
    concentration = max(counts.values()) / len(pods)
    return ServicePlacement(
        name=name,
        namespace="synthetic-viva",
        declared_replicas=len(pods),
        ready_replicas=len(pods),
        replica_concentration=concentration,
        concentrated=len(hosting_nodes) == 1,
        pods=pods,
        hosting_nodes=hosting_nodes,
        exposed_failure_domains=hosting_nodes if len(hosting_nodes) == 1 else [],
        deployment_names=[name],
    )


def build_disposable_inputs(
    scenario_id: str,
    *,
    generated_at: datetime,
) -> tuple[WorkflowGraph, DeploymentGraph, dict[str, object]]:
    """Build a complete deterministic scenario with explicit synthetic labels."""

    normalized_id = _normalized_scenario_id(scenario_id)
    generated_timestamp = _timestamp(generated_at)
    workflow = WorkflowGraph(
        workflow_id=f"synthetic-{normalized_id}-checkout",
        criticality=1.0,
        mandatory_services=[
            "frontend",
            "checkoutservice",
            "cartservice",
            "paymentservice",
        ],
        optional_services=["emailservice"],
        edges=[
            WorkflowEdge(source="frontend", target="checkoutservice"),
            WorkflowEdge(source="checkoutservice", target="cartservice"),
            WorkflowEdge(source="checkoutservice", target="paymentservice"),
            WorkflowEdge(source="checkoutservice", target="emailservice"),
        ],
    )
    deployment = DeploymentGraph(
        snapshot_id=f"synthetic-{normalized_id}-deployment",
        timestamp=generated_timestamp,
        cluster=ClusterMetadata(
            failure_domain_type="kubernetes.io/hostname",
            node_names=[
                "synthetic-node-1",
                "synthetic-node-2",
                "synthetic-node-3",
            ],
        ),
        nodes=[
            NodeModel(
                name="synthetic-node-1",
                zone="synthetic-zone-a",
                ready=True,
            ),
            NodeModel(
                name="synthetic-node-2",
                zone="synthetic-zone-b",
                ready=True,
            ),
            NodeModel(
                name="synthetic-node-3",
                zone="synthetic-zone-c",
                ready=True,
            ),
        ],
        services=[
            _placement(
                "frontend",
                [
                    _pod("frontend-1", "synthetic-node-2", "synthetic-zone-b"),
                    _pod("frontend-2", "synthetic-node-3", "synthetic-zone-c"),
                ],
            ),
            _placement(
                "checkoutservice",
                [
                    _pod(
                        "checkoutservice-1",
                        "synthetic-node-2",
                        "synthetic-zone-b",
                    ),
                    _pod(
                        "checkoutservice-2",
                        "synthetic-node-3",
                        "synthetic-zone-c",
                    ),
                ],
            ),
            _placement(
                "cartservice",
                [
                    _pod("cartservice-1", "synthetic-node-1", "synthetic-zone-a"),
                    _pod("cartservice-2", "synthetic-node-1", "synthetic-zone-a"),
                ],
            ),
            _placement(
                "paymentservice",
                [
                    _pod(
                        "paymentservice-1",
                        "synthetic-node-1",
                        "synthetic-zone-a",
                    ),
                    _pod(
                        "paymentservice-2",
                        "synthetic-node-2",
                        "synthetic-zone-b",
                    ),
                ],
            ),
            _placement(
                "emailservice",
                [
                    _pod(
                        "emailservice-1",
                        "synthetic-node-3",
                        "synthetic-zone-c",
                    ),
                ],
            ),
        ],
    )
    metadata: dict[str, object] = {
        "artifactType": ARTIFACT_TYPE,
        "evidenceStatus": EVIDENCE_STATUS,
        "disclaimer": DISCLAIMER,
        "scenarioId": normalized_id,
        "generatedAt": generated_timestamp,
        "workflowFile": "workflow-graph.json",
        "deploymentFile": "deployment-graph.json",
        "recommendedFailedNode": "synthetic-node-1",
        "dependencyScenarioServices": ["cartservice"],
        "expectedDemonstrationOutcome": {
            "failedServices": ["cartservice"],
            "failedWorkflows": [workflow.workflow_id],
        },
    }
    return workflow, deployment, metadata


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")


def write_disposable_inputs(
    output_dir: Path,
    scenario_id: str,
    *,
    generated_at: datetime,
) -> dict[str, object]:
    """Write one new disposable input set without replacing prior files."""

    workflow, deployment, metadata = build_disposable_inputs(
        scenario_id,
        generated_at=generated_at,
    )
    targets = {
        output_dir / "workflow-graph.json": workflow.model_dump(
            by_alias=True,
            mode="json",
        ),
        output_dir / "deployment-graph.json": deployment.model_dump(
            by_alias=True,
            mode="json",
        ),
        output_dir / "synthetic-input-metadata.json": metadata,
    }
    existing = tuple(str(path) for path in targets if path.exists())
    if existing:
        raise ValueError(
            "refusing to overwrite existing disposable input files: "
            + ", ".join(existing)
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        for path, payload in targets.items():
            _write_json(path, payload)
            created.append(path)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return metadata


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario-id",
        required=True,
        help="Short identifier embedded in the synthetic workflow and snapshot IDs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory that will receive the three disposable artifacts",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Generate, save, and describe one disposable input set."""

    arguments = _argument_parser().parse_args(argv)
    metadata = write_disposable_inputs(
        arguments.output_dir,
        arguments.scenario_id,
        generated_at=datetime.now(timezone.utc),
    )
    print(json.dumps(metadata, indent=2))
    return 0


def cli(argv: Sequence[str] | None = None) -> int:
    """Return concise messages for expected generation failures."""

    try:
        return main(argv)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
