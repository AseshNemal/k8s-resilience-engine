"""
Scenario 01: Baseline Initial Deployment.
Component: Component 2 - Deployment Topology Modeling
Captures the golden T0 reference state under clean, steady-state scheduling.
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


class Scenario01InitialDeployment(BaseScenario):
    """
    Scenario 01: Baseline Steady-State Initial Deployment.
    Verifies that in a clean cluster with healthy spreading, D_rate = 0.0.
    """

    def __init__(
        self,
        namespace: str = DEFAULT_CHAOS_NAMESPACE,
        execution_mode: ScenarioExecutionMode = ScenarioExecutionMode.DRY_RUN_SIMULATION,
        **kwargs: Any,
    ):
        super().__init__(
            scenario_id="01_initial_deployment",
            scenario_name="Baseline Initial Steady-State Deployment",
            disruption_type=DisruptionType.INITIAL_DEPLOYMENT,
            target_service="paymentservice",
            namespace=namespace,
            execution_mode=execution_mode,
            **kwargs,
        )

    def setup_workload(self) -> None:
        """Sets up a healthy 3-node cluster with distributed microservice replicas."""
        self._cluster_nodes = [
            {"name": "worker-1", "zone": "us-central1-a", "cpu": "4", "memory": "8Gi"},
            {"name": "worker-2", "zone": "us-central1-b", "cpu": "4", "memory": "8Gi"},
            {"name": "worker-3", "zone": "us-central1-c", "cpu": "4", "memory": "8Gi"},
        ]
        self._active_pods = [
            PodPlacementRecord(pod_name="payment-1", service_name="paymentservice", node_name="worker-1"),
            PodPlacementRecord(pod_name="payment-2", service_name="paymentservice", node_name="worker-2"),
            PodPlacementRecord(pod_name="cart-1", service_name="cartservice", node_name="worker-2"),
            PodPlacementRecord(pod_name="cart-2", service_name="cartservice", node_name="worker-3"),
            PodPlacementRecord(pod_name="checkout-1", service_name="checkoutservice", node_name="worker-1"),
        ]

    def execute_disruption(self) -> DisruptionEvent:
        """Baseline scenario executes a null disruption (observation only)."""
        return DisruptionEvent(
            event_id="evt-01-baseline",
            disruption_type=self.disruption_type,
            target_resource="cluster",
            parameters={"action": "none", "description": "Steady-state control verification"},
        )

    def rollback(self) -> bool:
        """No mutation performed; rollback returns True."""
        return True
