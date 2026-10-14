"""
Scenario 03: Scale-Down Redundancy Loss Scenario.
Component: Component 2 - Deployment Topology Modeling
Simulates scale-down eliminating multi-node redundancy, leaving a service with a single point of failure.
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


class Scenario03ScaleDownLoss(BaseScenario):
    """
    Scenario 03: Scale-Down Redundancy Loss.
    Evaluates transition from a resilient multi-node service into single-replica exposure.
    """

    def __init__(
        self,
        namespace: str = DEFAULT_CHAOS_NAMESPACE,
        execution_mode: ScenarioExecutionMode = ScenarioExecutionMode.DRY_RUN_SIMULATION,
        **kwargs: Any,
    ):
        super().__init__(
            scenario_id="03_scale_down_loss",
            scenario_name="Scale-Down Redundancy Loss",
            disruption_type=DisruptionType.SCALE_DOWN_LOSS,
            target_service="cartservice",
            namespace=namespace,
            execution_mode=execution_mode,
            **kwargs,
        )

    def setup_workload(self) -> None:
        """Sets up 3 cartservice replicas distributed across 3 distinct worker nodes."""
        self._cluster_nodes = [
            {"name": "worker-1", "zone": "us-central1-a", "cpu": "4", "memory": "8Gi"},
            {"name": "worker-2", "zone": "us-central1-b", "cpu": "4", "memory": "8Gi"},
            {"name": "worker-3", "zone": "us-central1-c", "cpu": "4", "memory": "8Gi"},
        ]
        self._active_pods = [
            PodPlacementRecord(pod_name="cart-1", service_name="cartservice", node_name="worker-1"),
            PodPlacementRecord(pod_name="cart-2", service_name="cartservice", node_name="worker-2"),
            PodPlacementRecord(pod_name="cart-3", service_name="cartservice", node_name="worker-3"),
            PodPlacementRecord(pod_name="frontend-1", service_name="frontend", node_name="worker-2"),
        ]

    def execute_disruption(self) -> DisruptionEvent:
        """Simulates scale-down: terminates cart-2 and cart-3, leaving single replica on worker-1."""
        self._active_pods = [
            p for p in self._active_pods
            if p.pod_name in {"cart-1", "frontend-1"}
        ]

        return DisruptionEvent(
            event_id="evt-03-scale-down",
            disruption_type=self.disruption_type,
            target_resource="cartservice",
            parameters={"pre_replicas": 3, "post_replicas": 1, "remaining_node": "worker-1"},
        )

    def rollback(self) -> bool:
        """Restores cartservice back to 3 replicas across worker-1, worker-2, and worker-3."""
        self.setup_workload()
        return True
