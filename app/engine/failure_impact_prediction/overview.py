"""Whole-cluster failure overview: every worker node assessed as the failed node, from one topology snapshot.

This is the answer the Failure impact page shows. It adds no new research rule: each node goes through the
same steps as a single prediction (``simulate_node_failure`` -> ``calculate_service_survival`` ->
``calculate_workflow_survival`` -> ``calculate_blast_radius``), and the service outcomes are the ones
``deployment_impact`` already defines. What it adds is composition: one pass over all nodes, with the workflows
that apply, so a person reads one ranked list instead of running one simulation per node.

Scope and honesty rules:

* A node failure is a *virtual* loss of the ready pods placed on it, before Kubernetes reschedules anything.
  Capacity, rescheduling and traffic are not modelled. The result is a prediction, never an observation.
* Without a workflow the answer is service-level only; workflow survival and blast radius stay ``None``
  (never 0, never 100). With workflows, blast radius is the existing definition: the share of the evaluated
  workflows that fail (``calculate_blast_radius``).
* A workflow whose services are not all placed is left out and reported with the reason; nothing is guessed.
* A workflow that is already not working (a mandatory service has no ready replica now) is reported apart and is
  not counted against any node: that fault exists before the node fails, so a node must not be blamed for it.
* Pure: reads nothing, changes nothing, never mutates its inputs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.engine.failure_impact_prediction.blast_radius import calculate_blast_radius
from app.engine.failure_impact_prediction.cross_layer_graph import (
    CrossLayerGraphError,
    build_cross_layer_graph,
)
from app.engine.failure_impact_prediction.deployment_impact import predict_node_service_impacts
from app.engine.failure_impact_prediction.models import CrossLayerGraph
from app.engine.failure_impact_prediction.node_failure_simulator import simulate_node_failure
from app.engine.failure_impact_prediction.service_survival import calculate_service_survival
from app.engine.failure_impact_prediction.workflow_survival import calculate_workflow_survival
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.workflow import WorkflowGraph

FROZEN = "frozen"
DECLARED = "declared"

ASSUMPTIONS = (
    "The prediction removes the ready pods placed on one worker node at once, before Kubernetes reschedules them.",
    "Only pods that are ready now count. Capacity, restarts and traffic are not modelled.",
    "One node fails at a time. This is a prediction from placement, not an observed failure.",
)


@dataclass(frozen=True)
class WorkflowBasis:
    """A workflow graph to evaluate and where it came from. ``kind`` is FROZEN (approved by Component 1) or DECLARED."""

    workflow: WorkflowGraph
    kind: str
    declared_by: str | None = None
    note: str | None = None
    display_name: str | None = None


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _service_reason(impact) -> str:
    if impact.outcome == "already_unavailable":
        return "No replica is ready now, so this node's loss changes nothing for it."
    if impact.outcome == "failed":
        return (f"All {_plural(impact.ready_before, 'ready replica')} run on this node, so the service has "
                "nothing left to answer with.")
    if impact.outcome == "degraded":
        return (f"{impact.lost_ready} of {impact.ready_before} ready replicas run on this node; "
                f"{impact.ready_after} keep serving.")
    return "None of its ready replicas run on this node."


def _service_rows(deployment: DeploymentGraph, node: str) -> list[dict[str, Any]]:
    rows = []
    for impact in predict_node_service_impacts(deployment, node):
        rows.append({
            "service": impact.service,
            "readyBefore": impact.ready_before,
            "lostReady": impact.lost_ready,
            "readyAfter": impact.ready_after,
            "outcome": impact.outcome,
            "reason": _service_reason(impact),
        })
    # Worst first, then affected, then the rest; alphabetical inside each group.
    order = {"failed": 0, "degraded": 1, "survives": 2, "already_unavailable": 3}
    rows.sort(key=lambda row: (order.get(row["outcome"], 9), row["service"]))
    return rows


def _workflow_rows(graph: CrossLayerGraph, failed_node: str):
    simulation = simulate_node_failure(graph, failed_node)
    services = calculate_service_survival(simulation)
    prediction = calculate_workflow_survival(graph, services)
    unavailable = [item.service for item in services if not item.survives]
    optional_down = [name for name in unavailable if name in graph.optional_services]
    return prediction, {
        "workflowId": graph.workflow_id,
        "survives": prediction.survives,
        "failedMandatoryServices": list(prediction.failed_due_to),
        "unavailableOptionalServices": optional_down,
    }


def _verdict(services_down: list[str], services_degraded: list[str], workflows_lost: int | None, hosts_pods: bool) -> str:
    if workflows_lost:
        return "workflow-lost"
    if services_down:
        return "service-down"
    if services_degraded:
        return "degraded"
    return "no-impact" if hosts_pods else "no-pods"


def _headline(node: str, verdict: str, services_down: list[str], services_degraded: list[str],
              workflows_lost: int | None, workflows_total: int) -> str:
    if verdict == "workflow-lost":
        return (f"If {node} stops, {workflows_lost} of {workflows_total} workflows would stop working "
                f"({', '.join(services_down)} unavailable).")
    if verdict == "service-down":
        tail = " No workflow depends on it." if workflows_total else " Workflow impact is unknown without a workflow."
        return f"If {node} stops, {', '.join(services_down)} becomes unavailable.{tail}"
    if verdict == "degraded":
        return (f"If {node} stops, every service keeps at least one ready replica; "
                f"{', '.join(services_degraded)} run with fewer.")
    if verdict == "no-pods":
        return f"{node} hosts no ready pods of this application, so its loss changes nothing here."
    return f"If {node} stops, no service of this application is affected."


def build_failure_overview(
    deployment: DeploymentGraph,
    workflows: Sequence[WorkflowBasis],
) -> dict[str, Any]:
    """Assess every worker node of ``deployment`` as the failed node. ``workflows`` must already be evaluable.

    A workflow that cannot be joined to the deployment (a service it names is not placed) is reported under
    ``workflowsNotEvaluated`` and takes no part in any count.
    """

    if not deployment.nodes:
        raise ValueError("The topology has no worker nodes, so there is no failure to assess.")

    graphs: list[tuple[WorkflowBasis, CrossLayerGraph]] = []
    not_evaluated: list[dict[str, Any]] = []
    already_broken: list[dict[str, Any]] = []
    for basis in workflows:
        try:
            graph = build_cross_layer_graph(basis.workflow, deployment)
        except CrossLayerGraphError as error:
            not_evaluated.append({"workflowId": basis.workflow.workflow_id, "kind": basis.kind, "reason": str(error)})
            continue
        down_now = [name for name in graph.mandatory_services
                    if not any(pod.ready for pod in (graph.service(name).pods if graph.service(name) else ()))]
        if down_now:
            already_broken.append({"workflowId": graph.workflow_id, "name": basis.display_name, "kind": basis.kind, "services": down_now,
                                   "reason": f"Not working now: {', '.join(down_now)} "
                                             f"{'has' if len(down_now) == 1 else 'have'} no ready replica."})
            continue
        graphs.append((basis, graph))

    pods_by_node: dict[str, int] = {}
    for service in deployment.services:
        for pod in service.pods:
            if pod.ready:
                pods_by_node[pod.node] = pods_by_node.get(pod.node, 0) + 1

    nodes: list[dict[str, Any]] = []
    for node in sorted(deployment.nodes, key=lambda item: item.name):
        service_rows = _service_rows(deployment, node.name)
        down = [row["service"] for row in service_rows if row["outcome"] == "failed"]
        degraded = [row["service"] for row in service_rows if row["outcome"] == "degraded"]

        workflow_rows: list[dict[str, Any]] = []
        predictions = []
        for basis, graph in graphs:
            prediction, row = _workflow_rows(graph, node.name)
            predictions.append(prediction)
            workflow_rows.append({**row, "kind": basis.kind, "criticality": graph.criticality, "name": basis.display_name})

        if predictions:
            blast_radius, survivability = calculate_blast_radius(tuple(predictions))
            workflows_lost: int | None = sum(not item.survives for item in predictions)
            weighted = _weighted_loss([graph.criticality for _, graph in graphs], predictions)
        else:
            blast_radius = survivability = workflows_lost = weighted = None

        hosted = pods_by_node.get(node.name, 0)
        verdict = _verdict(down, degraded, workflows_lost, hosted > 0)
        nodes.append({
            "node": node.name,
            "zone": node.zone,
            "ready": node.ready,
            "readyPodsHosted": hosted,
            "verdict": verdict,
            "headline": _headline(node.name, verdict, down, degraded, workflows_lost, len(predictions)),
            "servicesDown": down,
            "servicesDegraded": degraded,
            "services": service_rows,
            "workflows": workflow_rows,
            "workflowsLost": workflows_lost,
            "workflowsEvaluated": len(predictions),
            "blastRadius": blast_radius,
            "weightedBlastRadius": weighted,
            "survivability": survivability,
        })

    severity = {"workflow-lost": 0, "service-down": 1, "degraded": 2, "no-impact": 3, "no-pods": 4}
    nodes.sort(key=lambda row: (
        severity[row["verdict"]],
        -(row["blastRadius"] or 0.0),
        -len(row["servicesDown"]),
        -len(row["servicesDegraded"]),
        row["node"],
    ))
    for rank, row in enumerate(nodes, start=1):
        row["rank"] = rank

    return {
        "nodes": nodes,
        "singlePointsOfFailure": [row["node"] for row in nodes if row["verdict"] in ("workflow-lost", "service-down")],
        "services": _placement(deployment),
        "workflowsNotEvaluated": not_evaluated,
        "workflowsAlreadyBroken": already_broken,
        "workflowsEvaluated": [
            {"workflowId": basis.workflow.workflow_id, "kind": basis.kind, "criticality": graph.criticality,
             "mandatoryServices": list(graph.mandatory_services), "optionalServices": list(graph.optional_services),
             "declaredBy": basis.declared_by, "name": basis.display_name}
            for basis, graph in graphs
        ],
        "assumptions": list(ASSUMPTIONS),
        "scope": "workflow" if graphs else "service",
        "isPrediction": True,
    }


def _weighted_loss(criticalities: Sequence[float], predictions) -> float | None:
    """Percent of workflow importance lost: sum(criticality of failed workflows) / sum(criticality of all evaluated).

    The same definition Recommendations uses for several workflows (``portfolio._share``), so the two pages give one number
    for one failure. None when the criticalities sum to zero (nothing to weigh by), never 0.
    """
    total = sum(criticalities)
    if total <= 0:
        return None
    lost = sum(c for c, prediction in zip(criticalities, predictions, strict=True) if not prediction.survives)
    return round(lost / total * 100.0, 2)


def _placement(deployment: DeploymentGraph) -> list[dict[str, Any]]:
    """Where each service's ready replicas run, so a single-node service is visible before anything fails."""
    rows = []
    for service in sorted(deployment.services, key=lambda item: item.name):
        ready = [pod for pod in service.pods if pod.ready]
        per_node: dict[str, int] = {}
        for pod in ready:
            per_node[pod.node] = per_node.get(pod.node, 0) + 1
        rows.append({
            "service": service.name,
            "readyReplicas": len(ready),
            "declaredReplicas": service.declared_replicas,
            "nodes": [{"node": name, "readyPods": count} for name, count in sorted(per_node.items())],
            "singleNode": len(per_node) == 1 and len(ready) > 0,
        })
    return rows


__all__ = ["ASSUMPTIONS", "DECLARED", "FROZEN", "WorkflowBasis", "build_failure_overview"]
