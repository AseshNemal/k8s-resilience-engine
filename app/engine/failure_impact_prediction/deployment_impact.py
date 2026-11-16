from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.failure_impact import (
    DeploymentFailureImpactReport,
    DeploymentNodeRisk,
    DeploymentServiceImpact,
)


def _service_impact(service, failed_node: str) -> DeploymentServiceImpact:
    ready = [pod for pod in service.pods if pod.ready]
    lost = [pod for pod in ready if pod.node == failed_node]
    remaining = len(ready) - len(lost)
    outcome = "survives"
    if not ready:
        outcome = "already_unavailable"
    elif remaining == 0:
        outcome = "failed"
    elif lost:
        outcome = "degraded"
    return DeploymentServiceImpact(
        service=service.name,
        ready_before=len(ready),
        ready_after=remaining,
        lost_ready=len(lost),
        outcome=outcome,
    )


def _report_for_node(deployment: DeploymentGraph, failed_node: str) -> DeploymentFailureImpactReport:
    impacts = [_service_impact(service, failed_node) for service in deployment.services]
    evaluated = [impact for impact in impacts if impact.ready_before > 0]
    failed = [impact.service for impact in impacts if impact.outcome == "failed"]
    affected = [impact.service for impact in impacts if impact.lost_ready > 0]
    return DeploymentFailureImpactReport(
        failed_node=failed_node,
        services_evaluated=len(evaluated),
        failed_services=failed,
        affected_services=affected,
        already_unavailable_services=[
            impact.service for impact in impacts if impact.outcome == "already_unavailable"
        ],
        service_failure_rate=(
            len(failed) / len(evaluated) * 100 if evaluated else None
        ),
        service_impacts=impacts,
    )


def predict_deployment_failure_impact(
    deployment: DeploymentGraph,
    failed_node: str,
) -> DeploymentFailureImpactReport:
    """Predict ready-service outcomes without assuming any workflow dependencies."""
    node_names = {node.name for node in deployment.nodes}
    if failed_node not in node_names:
        raise ValueError(f"Failed node '{failed_node}' is not present in the deployment graph")

    report = _report_for_node(deployment, failed_node)
    rankings = []
    for node in deployment.nodes:
        node_report = _report_for_node(deployment, node.name)
        rankings.append(
            DeploymentNodeRisk(
                node=node.name,
                failed_services=node_report.failed_services,
                affected_services=node_report.affected_services,
                service_failure_rate=node_report.service_failure_rate,
            )
        )
    rankings.sort(
        key=lambda risk: (
            -len(risk.failed_services),
            -len(risk.affected_services),
            risk.node,
        )
    )
    return report.model_copy(update={"node_risk_ranking": rankings})
