"""Construct the Component 3 workflow-to-infrastructure graph."""

from app.engine.failure_impact_prediction.models import (
    CrossLayerEdge,
    CrossLayerGraph,
    CrossLayerNode,
    CrossLayerPod,
    CrossLayerService,
)
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.workflow import WorkflowGraph


class CrossLayerGraphError(ValueError):
    """Base error for invalid Component 1/2 integration data."""


class IncompleteTopologyError(CrossLayerGraphError):
    """Raised when a mandatory workflow service has no deployment topology."""


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def build_cross_layer_graph(
    workflow: WorkflowGraph,
    deployment: DeploymentGraph,
    *,
    require_complete: bool = True,
) -> CrossLayerGraph:
    """Join Component 1 and Component 2 outputs without mutating either input."""

    duplicate_mandatory_services = _duplicates(workflow.mandatory_services)
    if duplicate_mandatory_services:
        raise CrossLayerGraphError(
            "Duplicate mandatory workflow services: "
            + ", ".join(duplicate_mandatory_services)
        )

    duplicate_optional_services = _duplicates(workflow.optional_services)
    if duplicate_optional_services:
        raise CrossLayerGraphError(
            "Duplicate optional workflow services: "
            + ", ".join(duplicate_optional_services)
        )

    conflicting_services = sorted(
        set(workflow.mandatory_services) & set(workflow.optional_services)
    )
    if conflicting_services:
        raise CrossLayerGraphError(
            "Workflow services cannot be both mandatory and optional: "
            + ", ".join(conflicting_services)
        )

    workflow_edge_names = [
        f"{edge.source} -> {edge.target}" for edge in workflow.edges
    ]
    duplicate_workflow_edges = _duplicates(workflow_edge_names)
    if duplicate_workflow_edges:
        raise CrossLayerGraphError(
            "Duplicate workflow edges: "
            + ", ".join(duplicate_workflow_edges)
        )

    node_names = [node.name for node in deployment.nodes]
    duplicate_nodes = _duplicates(node_names)
    if duplicate_nodes:
        raise CrossLayerGraphError(
            f"Duplicate deployment node names: {', '.join(duplicate_nodes)}"
        )

    service_names = [service.name for service in deployment.services]
    duplicate_services = _duplicates(service_names)
    if duplicate_services:
        raise CrossLayerGraphError(
            f"Duplicate deployment service names: {', '.join(duplicate_services)}"
        )

    pod_names = [
        pod.name for service in deployment.services for pod in service.pods
    ]
    duplicate_pods = _duplicates(pod_names)
    if duplicate_pods:
        raise CrossLayerGraphError(
            f"Duplicate deployment pod names: {', '.join(duplicate_pods)}"
        )

    known_nodes = set(node_names)
    unknown_pod_nodes = sorted(
        {
            pod.node
            for service in deployment.services
            for pod in service.pods
            if pod.node not in known_nodes
        }
    )
    if unknown_pod_nodes:
        raise CrossLayerGraphError(
            "Pods reference unknown deployment nodes: "
            + ", ".join(unknown_pod_nodes)
        )

    referenced_services = set(workflow.mandatory_services)
    referenced_services.update(workflow.optional_services)
    for edge in workflow.edges:
        referenced_services.add(edge.source)
        referenced_services.add(edge.target)

    placement_by_name = {
        service.name: service for service in deployment.services
    }
    unresolved_services = tuple(
        sorted(referenced_services - placement_by_name.keys())
    )
    missing_mandatory = sorted(
        set(workflow.mandatory_services) - placement_by_name.keys()
    )
    if require_complete and missing_mandatory:
        raise IncompleteTopologyError(
            "Mandatory workflow services have no deployment topology: "
            + ", ".join(missing_mandatory)
        )

    graph_nodes: list[CrossLayerNode] = [
        CrossLayerNode(
            identifier=f"workflow:{workflow.workflow_id}",
            node_type="WORKFLOW",
            label=workflow.workflow_id,
        )
    ]
    graph_edges: list[CrossLayerEdge] = []

    for service_name in sorted(referenced_services):
        graph_nodes.append(
            CrossLayerNode(
                identifier=f"service:{service_name}",
                node_type="SERVICE",
                label=service_name,
            )
        )

    for service_name in workflow.mandatory_services:
        graph_edges.append(
            CrossLayerEdge(
                source=f"workflow:{workflow.workflow_id}",
                target=f"service:{service_name}",
                relationship="REQUIRES_MANDATORY",
            )
        )

    for service_name in workflow.optional_services:
        graph_edges.append(
            CrossLayerEdge(
                source=f"workflow:{workflow.workflow_id}",
                target=f"service:{service_name}",
                relationship="USES_OPTIONAL",
            )
        )

    for edge in workflow.edges:
        graph_edges.append(
            CrossLayerEdge(
                source=f"service:{edge.source}",
                target=f"service:{edge.target}",
                relationship="CALLS",
            )
        )

    failure_domains: set[str] = set()
    for node in deployment.nodes:
        graph_nodes.append(
            CrossLayerNode(
                identifier=f"node:{node.name}",
                node_type="NODE",
                label=node.name,
            )
        )
        if node.zone:
            failure_domains.add(node.zone)
            graph_edges.append(
                CrossLayerEdge(
                    source=f"node:{node.name}",
                    target=f"failure-domain:{node.zone}",
                    relationship="IN_FAILURE_DOMAIN",
                )
            )

    for failure_domain in sorted(failure_domains):
        graph_nodes.append(
            CrossLayerNode(
                identifier=f"failure-domain:{failure_domain}",
                node_type="FAILURE_DOMAIN",
                label=failure_domain,
            )
        )

    cross_layer_services: list[CrossLayerService] = []
    for service_name in sorted(referenced_services & placement_by_name.keys()):
        placement = placement_by_name[service_name]
        pods = tuple(
            CrossLayerPod(
                name=pod.name,
                node=pod.node,
                zone=pod.zone,
                ready=pod.ready,
            )
            for pod in placement.pods
        )
        cross_layer_services.append(
            CrossLayerService(
                name=placement.name,
                declared_replicas=placement.declared_replicas,
                ready_replicas=placement.ready_replicas,
                replica_concentration=placement.replica_concentration,
                pods=pods,
            )
        )
        for pod in pods:
            graph_nodes.append(
                CrossLayerNode(
                    identifier=f"pod:{pod.name}",
                    node_type="POD",
                    label=pod.name,
                )
            )
            graph_edges.extend(
                [
                    CrossLayerEdge(
                        source=f"service:{service_name}",
                        target=f"pod:{pod.name}",
                        relationship="HAS_REPLICA",
                    ),
                    CrossLayerEdge(
                        source=f"pod:{pod.name}",
                        target=f"node:{pod.node}",
                        relationship="RUNS_ON",
                    ),
                ]
            )

    return CrossLayerGraph(
        workflow_id=workflow.workflow_id,
        criticality=workflow.criticality,
        deployment_snapshot_id=deployment.snapshot_id,
        mandatory_services=tuple(workflow.mandatory_services),
        optional_services=tuple(workflow.optional_services),
        services=tuple(cross_layer_services),
        nodes=tuple(graph_nodes),
        edges=tuple(graph_edges),
        unresolved_services=unresolved_services,
    )
