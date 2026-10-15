"""
Scenario 04: Rolling Update Churn & Transient Skew Scenario.
Component: Component 2 - Deployment Topology Modeling
Simulates maxSurge rollout churn leading to transient co-location and skew across failure domains.
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


class Scenario04RollingUpdate(BaseScenario):
    """
    Scenario 04: Rolling Update Churn & Transient Skew.
    Evaluates placement drift during container image rolling update rollout churn.
    """

    def __init__(
        self,
        namespace: str = DEFAULT_CHAOS_NAMESPACE,
        execution_mode: ScenarioExecutionMode = ScenarioExecutionMode.DRY_RUN_SIMULATION,
        **kwargs: Any,
    ):
        super().__init__(
            scenario_id="04_rolling_update",
            scenario_name="Rolling Update Churn & Transient Skew",
            disruption_type=DisruptionType.ROLLING_UPDATE_CHURN,
            target_service="paymentservice",
            namespace=namespace,
            execution_mode=execution_mode,
            **kwargs,
        )

    def setup_workload(self) -> None:
        """Sets up 2 v1 payment pods evenly distributed across worker-1 and worker-2."""
        self._cluster_nodes = [
            {"name": "worker-1", "zone": "us-central1-a", "cpu": "4", "memory": "8Gi"},
            {"name": "worker-2", "zone": "us-central1-b", "cpu": "4", "memory": "8Gi"},
            {"name": "worker-3", "zone": "us-central1-c", "cpu": "4", "memory": "8Gi"},
        ]
        self._active_pods = [
            PodPlacementRecord(pod_name="payment-v1-1", service_name="paymentservice", node_name="worker-1"),
            PodPlacementRecord(pod_name="payment-v1-2", service_name="paymentservice", node_name="worker-2"),
            PodPlacementRecord(pod_name="checkout-1", service_name="checkoutservice", node_name="worker-3"),
        ]

    def execute_disruption(self) -> DisruptionEvent:
        """Simulates rollout: creates v2 pods on worker-2 while v1 terminates, causing co-location."""
        self._active_pods = [
            PodPlacementRecord(pod_name="payment-v2-1", service_name="paymentservice", node_name="worker-2"),
            PodPlacementRecord(pod_name="payment-v2-2", service_name="paymentservice", node_name="worker-2"),
            PodPlacementRecord(pod_name="checkout-1", service_name="checkoutservice", node_name="worker-3"),
        ]

        return DisruptionEvent(
            event_id="evt-04-rolling-update",
            disruption_type=self.disruption_type,
            target_resource="paymentservice",
            parameters={
                "strategy": "RollingUpdate",
                "max_surge": "25%",
                "transient_node": "worker-2",
            },
        )

    def rollback(self) -> bool:
        """Restores v1 deployment baseline."""
        self.setup_workload()
        return True
