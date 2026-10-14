"""
Scenario 06: Node Cordon Scenario.
Component: Component 2 - Deployment Topology Modeling
Simulates marking a worker node as unschedulable (SchedulingDisabled) and observing placement diversion.
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


class Scenario06NodeCordon(BaseScenario):
    """
    Scenario 06: Node Cordon Scenario.
    Marks worker-2 as unschedulable; tests whether newly scaled pods divert to worker-1.
    """

    def __init__(
        self,
        namespace: str = DEFAULT_CHAOS_NAMESPACE,
        execution_mode: ScenarioExecutionMode = ScenarioExecutionMode.DRY_RUN_SIMULATION,
        **kwargs: Any,
    ):
        super().__init__(
            scenario_id="06_node_cordon",
            scenario_name="Node Cordon Unschedulable Diversion",
            disruption_type=DisruptionType.NODE_CORDON,
            target_service="paymentservice",
            namespace=namespace,
            execution_mode=execution_mode,
            **kwargs,
        )

    def setup_workload(self) -> None:
        """Sets up 3 worker nodes with payment and cart pods."""
        self._cluster_nodes = [
            {"name": "worker-1", "zone": "us-central1-a", "cpu": "4", "memory": "8Gi", "schedulable": True},
            {"name": "worker-2", "zone": "us-central1-b", "cpu": "4", "memory": "8Gi", "schedulable": True},
            {"name": "worker-3", "zone": "us-central1-c", "cpu": "4", "memory": "8Gi", "schedulable": True},
        ]
        self._active_pods = [
            PodPlacementRecord(pod_name="payment-1", service_name="paymentservice", node_name="worker-1"),
            PodPlacementRecord(pod_name="payment-2", service_name="paymentservice", node_name="worker-2"),
            PodPlacementRecord(pod_name="cart-1", service_name="cartservice", node_name="worker-3"),
        ]

    def execute_disruption(self) -> DisruptionEvent:
        """Cordons worker-2; scales payment by adding payment-3 which must divert to worker-1."""
        for n in self._cluster_nodes:
            if n["name"] == "worker-2":
                n["schedulable"] = False

        # Add new payment-3 pod: cannot land on worker-2, so lands on worker-1
        self._active_pods.append(
            PodPlacementRecord(pod_name="payment-3", service_name="paymentservice", node_name="worker-1")
        )

        return DisruptionEvent(
            event_id="evt-06-node-cordon",
            disruption_type=self.disruption_type,
            target_resource="worker-2",
            parameters={
                "action": "cordon",
                "cordoned_node": "worker-2",
                "diverted_pod": "payment-3",
                "destination_node": "worker-1",
            },
        )

    def rollback(self) -> bool:
        """Uncordons worker-2 and restores baseline pods."""
        self.setup_workload()
        return True
