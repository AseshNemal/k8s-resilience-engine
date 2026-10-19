"""Virtual single-worker-node failure simulation."""

from app.engine.failure_impact_prediction.models import (
    CrossLayerGraph,
    NodeFailureSimulation,
    ServiceReplicaState,
)


def simulate_node_failure(
    graph: CrossLayerGraph,
    failed_node: str,
) -> NodeFailureSimulation:
    """Virtually remove ready pods on ``failed_node`` from an immutable graph."""

    if failed_node not in graph.node_names:
        available = ", ".join(graph.node_names) or "none"
        raise ValueError(
            f"Unknown failed node '{failed_node}'. Available nodes: {available}"
        )

    states: list[ServiceReplicaState] = []
    for service in graph.services:
        initially_ready = tuple(
            pod.name for pod in service.pods if pod.ready
        )
        lost = tuple(
            pod.name
            for pod in service.pods
            if pod.ready and pod.node == failed_node
        )
        surviving = tuple(
            pod.name
            for pod in service.pods
            if pod.ready and pod.node != failed_node
        )
        states.append(
            ServiceReplicaState(
                service=service.name,
                initially_ready_pods=initially_ready,
                lost_ready_pods=lost,
                surviving_ready_pods=surviving,
            )
        )

    return NodeFailureSimulation(
        failed_node=failed_node,
        service_states=tuple(states),
    )
