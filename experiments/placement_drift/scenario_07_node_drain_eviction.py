"""
Scenario 07: Node Drain & Pod Evacuation Scenario.
Component: Component 2 - Deployment Topology Modeling
Simulates draining an entire worker node and forces mass pod migration to surviving worker nodes.
"""
from typing import Any, Dict, List

from experiments.placement_drift.base_scenario import BaseScenario
from experiments.placement_drift.constants import (
    DEFAULT_CHAOS_NAMESPACE,
    DisruptionType,
    ScenarioExecutionMode,
)
from experiments.placement_drift.scenario_models import (
    DisruptionEvent,
    PodPlacementRecord,
)


class Scenario07NodeDrainEviction(BaseScenario):
    """
    Scenario 07: Node Drain & Pod Evacuation.
    Drains worker-1, evacuating its pods to worker-2 and worker-3.
    """

    def __init__(
        self,
        namespace: str = DEFAULT_CHAOS_NAMESPACE,
        execution_mode: ScenarioExecutionMode = ScenarioExecutionMode.DRY_RUN_SIMULATION,
        **kwargs: Any,
    ):
        super().__init__(
            scenario_id="07_node_drain_eviction",
            scenario_name="Node Drain & Mass Pod Evacuation",
            disruption_type=DisruptionType.NODE_DRAIN_EVICTION,
            target_service="paymentservice",
            namespace=namespace,
            execution_mode=execution_mode,
            **kwargs,
        )

    def setup_workload(self) -> None:
        """Sets up 4 pods across 3 worker nodes, with payment-1 and cart-1 on worker-1."""
        self._cluster_nodes = [
            {"name": "worker-1", "zone": "us-central1-a", "cpu": "4", "memory": "8Gi", "drained": False},
            {"name": "worker-2", "zone": "us-central1-b", "cpu": "4", "memory": "8Gi", "drained": False},
            {"name": "worker-3", "zone": "us-central1-c", "cpu": "4", "memory": "8Gi", "drained": False},
        ]
        self._active_pods = [
            PodPlacementRecord(pod_name="payment-1", service_name="paymentservice", node_name="worker-1"),
            PodPlacementRecord(pod_name="payment-2", service_name="paymentservice", node_name="worker-2"),
            PodPlacementRecord(pod_name="cart-1", service_name="cartservice", node_name="worker-1"),
            PodPlacementRecord(pod_name="checkout-1", service_name="checkoutservice", node_name="worker-3"),
        ]

    def execute_disruption(self) -> DisruptionEvent:
        """Drains worker-1: payment-1 moves to worker-2 (causing co-location), cart-1 moves to worker-3."""
        for n in self._cluster_nodes:
            if n["name"] == "worker-1":
                n["drained"] = True

        updated_pods: List[PodPlacementRecord] = []
        for p in self._active_pods:
            if p.pod_name == "payment-1":
                # Migrated to worker-2 -> now both payment pods are on worker-2!
                updated_pods.append(
                    PodPlacementRecord(pod_name="payment-1", service_name="paymentservice", node_name="worker-2")
                )
            elif p.pod_name == "cart-1":
                # Migrated to worker-3
                updated_pods.append(
                    PodPlacementRecord(pod_name="cart-1", service_name="cartservice", node_name="worker-3")
                )
            else:
                updated_pods.append(p)

        self._active_pods = updated_pods

        return DisruptionEvent(
            event_id="evt-07-node-drain",
            disruption_type=self.disruption_type,
            target_resource="worker-1",
            parameters={
                "action": "drain",
                "drained_node": "worker-1",
                "evacuated_pods": ["payment-1", "cart-1"],
                "migration_paths": {
                    "payment-1": "worker-1 -> worker-2",
                    "cart-1": "worker-1 -> worker-3",
                },
            },
        )

    def rollback(self) -> bool:
        """Un-drains worker-1 and restores baseline pod distribution."""
        self.setup_workload()
        return True
