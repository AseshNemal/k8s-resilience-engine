"""Build and safely write research artifacts for method comparisons."""

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from app.engine.failure_impact_prediction.baseline_comparison import (
    FailureImpactMethodComparison,
)


ARTIFACT_TYPE = "FAILURE_IMPACT_METHOD_COMPARISON"
METHODOLOGY_VERSION = "1.0"


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Comparison artifact timestamps must include timezone information")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_method_comparison_export(
    comparison: FailureImpactMethodComparison,
    *,
    generated_at: datetime,
) -> dict[str, object]:
    """Return a JSON-ready artifact while preserving baseline limitations."""

    replica = comparison.replica_count_only
    deployment = comparison.deployment_only
    dependency = comparison.dependency_only
    proposed = comparison.proposed_cross_layer

    if replica.deployment_snapshot_id != comparison.deployment_snapshot_id:
        raise ValueError(
            "Replica-count result and comparison must use the same deployment snapshot"
        )
    if deployment.deployment_snapshot_id != comparison.deployment_snapshot_id:
        raise ValueError(
            "Deployment-only result and comparison must use the same deployment snapshot"
        )
    if deployment.failed_node != comparison.failed_node:
        raise ValueError(
            "Deployment-only result and comparison must use the same failed node"
        )
    if dependency.workflow_id != comparison.workflow_id:
        raise ValueError(
            "Dependency-only result and comparison must use the same workflow"
        )
    if proposed.workflow_id != comparison.workflow_id:
        raise ValueError(
            "Cross-layer result and comparison must use the same workflow"
        )
    if proposed.failed_node != comparison.failed_node:
        raise ValueError(
            "Cross-layer result and comparison must use the same failed node"
        )

    return {
        "artifactType": ARTIFACT_TYPE,
        "methodologyVersion": METHODOLOGY_VERSION,
        "generatedAt": _timestamp(generated_at),
        "workflowId": comparison.workflow_id,
        "deploymentSnapshotId": comparison.deployment_snapshot_id,
        "failedNode": comparison.failed_node,
        "dependencyScenarioServices": list(
            comparison.dependency_scenario_services
        ),
        "methods": {
            "replicaCountOnly": {
                "evidenceScope": "ready replica counts; no pod placement or workflow dependencies",
                "unavailableServices": list(replica.unavailable_services),
                "singleReplicaServices": list(replica.single_replica_services),
                "vulnerableServices": list(replica.vulnerable_services),
                "vulnerableServicePercentage": (
                    replica.vulnerable_service_percentage
                ),
            },
            "deploymentOnly": {
                "evidenceScope": "ready pod placement; no workflow dependencies",
                "nodeInducedFailures": list(deployment.node_induced_failures),
                "preexistingUnavailableServices": list(
                    deployment.preexisting_unavailable_services
                ),
                "unavailableServices": list(deployment.unavailable_services),
                "unavailableServicePercentage": (
                    deployment.unavailable_service_percentage
                ),
            },
            "dependencyOnly": {
                "evidenceScope": (
                    "workflow dependencies and a predeclared service-failure "
                    "scenario; no deployment placement"
                ),
                "unavailableServices": list(dependency.unavailable_services),
                "failedMandatoryServices": list(
                    dependency.failed_mandatory_services
                ),
                "unavailableOptionalServices": list(
                    dependency.unavailable_optional_services
                ),
                "unreferencedServices": list(
                    dependency.unreferenced_services
                ),
                "survives": dependency.survives,
            },
            "proposedCrossLayer": {
                "evidenceScope": (
                    "workflow dependencies joined with ready pod placement"
                ),
                **proposed.model_dump(by_alias=True),
            },
        },
    }


def write_method_comparison_artifact(
    path: Path,
    artifact: Mapping[str, object],
) -> Path:
    """Write a new UTF-8 artifact without replacing an earlier research run."""

    if artifact.get("artifactType") != ARTIFACT_TYPE:
        raise ValueError(
            f"comparison artifact must have artifactType {ARTIFACT_TYPE}"
        )

    created = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            json.dump(artifact, stream, indent=2)
            stream.write("\n")
    except FileExistsError as error:
        raise ValueError(
            f"refusing to overwrite existing comparison artifact: {path}"
        ) from error
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
    return path


__all__ = [
    "ARTIFACT_TYPE",
    "METHODOLOGY_VERSION",
    "build_method_comparison_export",
    "write_method_comparison_artifact",
]
