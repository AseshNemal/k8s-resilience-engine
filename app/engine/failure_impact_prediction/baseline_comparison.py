"""Run research baselines beside the proposed cross-layer method."""

from collections.abc import Collection
from dataclasses import dataclass

from app.engine.failure_impact_prediction.baseline_analysis import (
    DependencyOnlyBaseline,
    DeploymentExposureBaseline,
    ReplicaCountBaseline,
    analyze_dependency_only,
    analyze_deployment_only,
    analyze_replica_count_only,
)
from app.engine.failure_impact_prediction.service import (
    predict_failure_impact,
)
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.failure_impact import FailureImpactReport
from app.shared.schemas.workflow import WorkflowGraph


@dataclass(frozen=True)
class FailureImpactMethodComparison:
    """Outputs from four methods evaluated for one declared scenario.

    The baseline outputs remain separate because they answer different limited
    questions.  In particular, a replica-count vulnerability is not relabelled
    as an observed failure, and deployment-only analysis does not claim a
    workflow outcome without workflow evidence.
    """

    workflow_id: str
    deployment_snapshot_id: str
    failed_node: str
    dependency_scenario_services: tuple[str, ...]
    replica_count_only: ReplicaCountBaseline
    deployment_only: DeploymentExposureBaseline
    dependency_only: DependencyOnlyBaseline
    proposed_cross_layer: FailureImpactReport


def compare_failure_impact_methods(
    workflow: WorkflowGraph,
    deployment: DeploymentGraph,
    failed_node: str,
    *,
    dependency_scenario_services: Collection[str],
) -> FailureImpactMethodComparison:
    """Evaluate all planned methods without changing either upstream input.

    ``dependency_scenario_services`` is explicit so that a dependency-only
    experiment uses a scenario chosen before the failure.  Supplying observed
    post-failure services here would leak ground truth into that baseline.
    """

    scenario_services = tuple(sorted(set(dependency_scenario_services)))

    return FailureImpactMethodComparison(
        workflow_id=workflow.workflow_id,
        deployment_snapshot_id=deployment.snapshot_id,
        failed_node=failed_node,
        dependency_scenario_services=scenario_services,
        replica_count_only=analyze_replica_count_only(deployment),
        deployment_only=analyze_deployment_only(deployment, failed_node),
        dependency_only=analyze_dependency_only(
            workflow,
            scenario_services,
        ),
        proposed_cross_layer=predict_failure_impact(
            workflow,
            deployment,
            failed_node,
        ),
    )


__all__ = [
    "FailureImpactMethodComparison",
    "compare_failure_impact_methods",
]
