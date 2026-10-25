"""Build and evaluate a disposable multi-workflow Component 3 portfolio."""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.failure_impact_prediction.cross_layer_graph import (  # noqa: E402
    build_cross_layer_graph,
)
from app.engine.failure_impact_prediction.models import CrossLayerGraph  # noqa: E402
from app.engine.failure_impact_prediction.portfolio_impact import (  # noqa: E402
    predict_portfolio_failure_impact,
)
from app.engine.failure_impact_prediction.service import (  # noqa: E402
    build_portfolio_failure_impact_report,
    predict_failure_impact,
)
from app.shared.schemas.deployment import DeploymentGraph  # noqa: E402
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph  # noqa: E402
from experiments.failure_impact_validation.generate_disposable_inputs import (  # noqa: E402
    DISCLAIMER,
    EVIDENCE_STATUS,
    build_disposable_inputs,
)


WORKFLOWS_FILE = "workflow-graphs.json"
DEPLOYMENT_FILE = "deployment-graph.json"
METADATA_FILE = "synthetic-portfolio-metadata.json"
CROSS_LAYER_FILE = "cross-layer-graphs.json"
RESULT_FILE = "portfolio-scenario-results.json"
COMPONENT4_HANDOFF_FILE = "component-4-handoff.json"

INPUT_ARTIFACT_TYPE = "DISPOSABLE_COMPONENT3_PORTFOLIO_INPUTS"
CROSS_LAYER_ARTIFACT_TYPE = "DISPOSABLE_COMPONENT3_CROSS_LAYER_GRAPHS"
RESULT_ARTIFACT_TYPE = "DISPOSABLE_COMPONENT3_PORTFOLIO_RESULTS"
COMPONENT4_HANDOFF_ARTIFACT_TYPE = (
    "DISPOSABLE_COMPONENT3_TO_COMPONENT4_HANDOFF"
)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("generated_at must include timezone information")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _portfolio_workflows(
    scenario_id: str,
    checkout: WorkflowGraph,
) -> tuple[WorkflowGraph, ...]:
    prefix = f"synthetic-{scenario_id}"
    return (
        checkout,
        WorkflowGraph(
            workflow_id=f"{prefix}-cart-management",
            criticality=0.8,
            mandatory_services=["frontend", "cartservice"],
            optional_services=["emailservice"],
            edges=[
                WorkflowEdge(source="frontend", target="cartservice"),
                WorkflowEdge(source="cartservice", target="emailservice"),
            ],
        ),
        WorkflowGraph(
            workflow_id=f"{prefix}-payment-status",
            criticality=0.7,
            mandatory_services=["frontend", "paymentservice"],
            optional_services=[],
            edges=[
                WorkflowEdge(source="frontend", target="paymentservice"),
            ],
        ),
        WorkflowGraph(
            workflow_id=f"{prefix}-email-notification",
            criticality=0.6,
            mandatory_services=["frontend", "emailservice"],
            optional_services=[],
            edges=[
                WorkflowEdge(source="frontend", target="emailservice"),
            ],
        ),
    )


def build_disposable_portfolio_inputs(
    scenario_id: str,
    *,
    generated_at: datetime,
) -> tuple[tuple[WorkflowGraph, ...], DeploymentGraph, dict[str, object]]:
    """Create four disclosed workflow graphs over one synthetic deployment."""

    checkout, deployment, base_metadata = build_disposable_inputs(
        scenario_id,
        generated_at=generated_at,
    )
    normalized_id = str(base_metadata["scenarioId"])
    workflows = _portfolio_workflows(normalized_id, checkout)
    metadata: dict[str, object] = {
        "artifactType": INPUT_ARTIFACT_TYPE,
        "evidenceStatus": EVIDENCE_STATUS,
        "disclaimer": DISCLAIMER,
        "scenarioId": normalized_id,
        "generatedAt": _timestamp(generated_at),
        "workflowFile": WORKFLOWS_FILE,
        "deploymentFile": DEPLOYMENT_FILE,
        "crossLayerFile": CROSS_LAYER_FILE,
        "resultFile": RESULT_FILE,
        "workflowIds": [workflow.workflow_id for workflow in workflows],
        "expectedDemonstrationOutcomes": {
            "synthetic-node-1": {
                "failedWorkflowCount": 2,
                "blastRadius": 50.0,
            },
            "synthetic-node-2": {
                "failedWorkflowCount": 0,
                "blastRadius": 0.0,
            },
            "synthetic-node-3": {
                "failedWorkflowCount": 1,
                "blastRadius": 25.0,
            },
        },
    }
    return workflows, deployment, metadata


def _cross_layer_graph_export(graph: CrossLayerGraph) -> dict[str, object]:
    return {
        "workflowId": graph.workflow_id,
        "criticality": graph.criticality,
        "deploymentSnapshotId": graph.deployment_snapshot_id,
        "mandatoryServices": list(graph.mandatory_services),
        "optionalServices": list(graph.optional_services),
        "unresolvedServices": list(graph.unresolved_services),
        "nodes": [
            {
                "identifier": node.identifier,
                "nodeType": node.node_type,
                "label": node.label,
            }
            for node in graph.nodes
        ],
        "edges": [
            {
                "source": edge.source,
                "target": edge.target,
                "relationship": edge.relationship,
            }
            for edge in graph.edges
        ],
        "services": [
            {
                "name": service.name,
                "declaredReplicas": service.declared_replicas,
                "readyReplicas": service.ready_replicas,
                "replicaConcentration": service.replica_concentration,
                "pods": [
                    {
                        "name": pod.name,
                        "node": pod.node,
                        "zone": pod.zone,
                        "ready": pod.ready,
                    }
                    for pod in service.pods
                ],
            }
            for service in graph.services
        ],
    }


def _selected_nodes(
    deployment: DeploymentGraph,
    requested_nodes: Sequence[str],
) -> tuple[str, ...]:
    available = tuple(sorted(node.name for node in deployment.nodes))
    selected_values: set[str] = set()
    for value in requested_nodes:
        node = value.strip()
        if not node:
            raise ValueError("failed nodes must be non-empty strings")
        selected_values.add(node)
    selected = tuple(sorted(selected_values)) or available
    unknown = tuple(sorted(set(selected) - set(available)))
    if unknown:
        raise ValueError("unknown failed nodes: " + ", ".join(unknown))
    return selected


def _build_disposable_portfolio_generation(
    *,
    scenario_id: str,
    generated_at: datetime,
) -> tuple[
    tuple[WorkflowGraph, ...],
    DeploymentGraph,
    dict[str, Mapping[str, object] | list[object]],
]:
    """Build the disclosed inputs and their cross-layer graphs in memory."""

    workflows, deployment, metadata = build_disposable_portfolio_inputs(
        scenario_id,
        generated_at=generated_at,
    )
    timestamp = _timestamp(generated_at)
    graphs = tuple(
        build_cross_layer_graph(workflow, deployment)
        for workflow in workflows
    )
    workflow_artifact: dict[str, object] = {
        "artifactType": INPUT_ARTIFACT_TYPE,
        "evidenceStatus": EVIDENCE_STATUS,
        "disclaimer": DISCLAIMER,
        "scenarioId": metadata["scenarioId"],
        "generatedAt": timestamp,
        "workflows": [
            workflow.model_dump(by_alias=True, mode="json")
            for workflow in workflows
        ],
    }
    cross_layer_artifact: dict[str, object] = {
        "artifactType": CROSS_LAYER_ARTIFACT_TYPE,
        "evidenceStatus": EVIDENCE_STATUS,
        "disclaimer": DISCLAIMER,
        "scenarioId": metadata["scenarioId"],
        "generatedAt": timestamp,
        "deploymentSnapshotId": deployment.snapshot_id,
        "workflowCount": len(graphs),
        "graphs": [_cross_layer_graph_export(graph) for graph in graphs],
    }
    return (
        workflows,
        deployment,
        {
            WORKFLOWS_FILE: workflow_artifact,
            DEPLOYMENT_FILE: deployment.model_dump(by_alias=True, mode="json"),
            METADATA_FILE: metadata,
            CROSS_LAYER_FILE: cross_layer_artifact,
        },
    )


def build_disposable_portfolio_generation_artifacts(
    *,
    scenario_id: str,
    generated_at: datetime,
) -> dict[str, Mapping[str, object] | list[object]]:
    """Return synthetic inputs and all cross-layer graphs without a failure run."""

    _, _, artifacts = _build_disposable_portfolio_generation(
        scenario_id=scenario_id,
        generated_at=generated_at,
    )
    return artifacts


def build_disposable_portfolio_artifacts(
    *,
    scenario_id: str,
    failed_nodes: Sequence[str],
    generated_at: datetime,
) -> dict[str, Mapping[str, object] | list[object]]:
    """Build input, cross-layer, and portfolio-result artifacts in memory."""

    workflows, deployment, artifacts = _build_disposable_portfolio_generation(
        scenario_id=scenario_id,
        generated_at=generated_at,
    )
    metadata = artifacts[METADATA_FILE]
    if not isinstance(metadata, Mapping):
        raise ValueError("portfolio metadata artifact must be a JSON object")
    timestamp = _timestamp(generated_at)
    selected = _selected_nodes(deployment, failed_nodes)
    reports = tuple(
        build_portfolio_failure_impact_report(
            predict_portfolio_failure_impact(
                workflows,
                deployment,
                failed_node,
            )
        )
        for failed_node in selected
    )
    result_payloads = [
        report.model_dump(by_alias=True, mode="json")
        for report in reports
    ]
    result_artifact: dict[str, object] = {
        "artifactType": RESULT_ARTIFACT_TYPE,
        "evidenceStatus": EVIDENCE_STATUS,
        "disclaimer": DISCLAIMER,
        "scenarioId": metadata["scenarioId"],
        "generatedAt": timestamp,
        "deploymentSnapshotId": deployment.snapshot_id,
        "totalWorkflows": len(workflows),
        "evaluatedNodes": list(selected),
        "results": result_payloads,
    }
    failure_impact_reports = [
        predict_failure_impact(workflow, deployment, failed_node).model_dump(
            by_alias=True,
            mode="json",
        )
        for failed_node in selected
        for workflow in workflows
    ]
    handoff_artifact: dict[str, object] = {
        "artifactType": COMPONENT4_HANDOFF_ARTIFACT_TYPE,
        "evidenceStatus": EVIDENCE_STATUS,
        "disclaimer": DISCLAIMER,
        "scenarioId": metadata["scenarioId"],
        "generatedAt": timestamp,
        "targetComponent": "recommendation_evaluation_validation",
        "entryPoint": (
            "app.engine.recommendation_evaluation_validation.service."
            "evaluate_first_recommendation"
        ),
        "invocationModel": (
            "Pair the shared deploymentGraph with one failureImpactReport "
            "for each Component 4 evaluation."
        ),
        "invocationCount": len(failure_impact_reports),
        "deploymentGraph": deployment.model_dump(by_alias=True, mode="json"),
        "failureImpactReports": failure_impact_reports,
    }
    artifacts[RESULT_FILE] = result_artifact
    artifacts[COMPONENT4_HANDOFF_FILE] = handoff_artifact
    return artifacts


def write_disposable_portfolio_demo(
    *,
    scenario_id: str,
    output_dir: Path,
    failed_nodes: Sequence[str],
    generated_at: datetime,
) -> dict[str, Mapping[str, object] | list[object]]:
    """Write one complete portfolio bundle without replacing prior evidence."""

    targets = tuple(
        output_dir / name
        for name in (
            WORKFLOWS_FILE,
            DEPLOYMENT_FILE,
            METADATA_FILE,
            CROSS_LAYER_FILE,
            RESULT_FILE,
            COMPONENT4_HANDOFF_FILE,
        )
    )
    existing = tuple(str(path) for path in targets if path.exists())
    if existing:
        raise ValueError(
            "refusing to overwrite existing disposable portfolio files: "
            + ", ".join(existing)
        )

    artifacts = build_disposable_portfolio_artifacts(
        scenario_id=scenario_id,
        failed_nodes=failed_nodes,
        generated_at=generated_at,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        for filename, payload in artifacts.items():
            path = output_dir / filename
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, indent=2)
                stream.write("\n")
            created.append(path)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return artifacts


def format_compact_summary(result_artifact: Mapping[str, object]) -> str:
    """Create a viva-friendly table from the portfolio result artifact."""

    payloads = result_artifact.get("results")
    if not isinstance(payloads, list) or not payloads:
        raise ValueError("portfolio output requires at least one result")
    lines = [
        f"Component 3 disposable portfolio: {result_artifact.get('scenarioId')}",
        f"Evidence status: {EVIDENCE_STATUS}",
        f"Workflows evaluated: {result_artifact.get('totalWorkflows')}",
        f"Disclaimer: {DISCLAIMER}",
        "",
        (
            "Node | Failed workflows | Blast radius | Survivability | "
            "Failed services"
        ),
    ]
    for payload in payloads:
        if not isinstance(payload, Mapping):
            raise ValueError("portfolio result entries must be JSON objects")
        failed_workflows = payload.get("failedWorkflows")
        failed_services = payload.get("failedServices")
        workflow_text = (
            ", ".join(map(str, failed_workflows))
            if isinstance(failed_workflows, list) and failed_workflows
            else "none"
        )
        service_text = (
            ", ".join(map(str, failed_services))
            if isinstance(failed_services, list) and failed_services
            else "none"
        )
        lines.append(
            f"{payload.get('failedNode')} | {workflow_text} | "
            f"{float(payload.get('blastRadius', 0.0)):.1f}% | "
            f"{float(payload.get('survivability', 0.0)):.1f}% | "
            f"{service_text}"
        )
    return "\n".join(lines)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario-id",
        required=True,
        help="Short identifier embedded in the disposable artifact IDs",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="New directory that will receive the complete portfolio bundle",
    )
    parser.add_argument(
        "--failed-node",
        action="append",
        default=[],
        help="Node to fail; repeat for several nodes or omit for every node",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print a concise table; the complete artifacts remain JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    artifacts = write_disposable_portfolio_demo(
        scenario_id=arguments.scenario_id,
        output_dir=arguments.output_dir,
        failed_nodes=arguments.failed_node,
        generated_at=datetime.now(timezone.utc),
    )
    result = artifacts[RESULT_FILE]
    if not isinstance(result, Mapping):
        raise ValueError("portfolio result artifact must be a JSON object")
    if arguments.compact:
        print(format_compact_summary(result))
    else:
        print(json.dumps(result, indent=2))
    return 0


def cli(argv: Sequence[str] | None = None) -> int:
    try:
        return main(argv)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
