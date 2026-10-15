"""
Scenario 02: Scale-Up Bin-Packing Stress Scenario.
Component: Component 2 - Deployment Topology Modeling
Simulates horizontal pod autoscaling (HPA) scale-up causing scheduler bin-packing concentration.
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


class Scenario02ScaleUpStress(BaseScenario):
    """
    Scenario 02: Scale-Up Bin-Packing Stress.
    Tests whether sudden scale-up causes replicas to bin-pack onto a single worker node.
    """

    def __init__(
        self,
        namespace: str = DEFAULT_CHAOS_NAMESPACE,
        execution_mode: ScenarioExecutionMode = ScenarioExecutionMode.DRY_RUN_SIMULATION,
        **kwargs: Any,
    ):
        super().__init__(
            scenario_id="02_scale_up_stress",
            scenario_name="HPA Scale-Up Bin-Packing Stress",
            disruption_type=DisruptionType.SCALE_UP_STRESS,
            target_service="paymentservice",
            namespace=namespace,
            execution_mode=execution_mode,
            **kwargs,
        )
        self._initial_pods_count = 2
        self._scaled_pods_count = 5

    def setup_workload(self) -> None:
        """Sets up 2 initial payment replicas spread across worker-1 and worker-2."""
        self._cluster_nodes = [
            {"name": "worker-1", "zone": "us-central1-a", "cpu": "8", "memory": "16Gi"},
            {"name": "worker-2", "zone": "us-central1-b", "cpu": "2", "memory": "4Gi"},
            {"name": "worker-3", "zone": "us-central1-c", "cpu": "2", "memory": "4Gi"},
        ]
        self._active_pods = [
            PodPlacementRecord(pod_name="payment-1", service_name="paymentservice", node_name="worker-1"),
            PodPlacementRecord(pod_name="payment-2", service_name="paymentservice", node_name="worker-2"),
            PodPlacementRecord(pod_name="cart-1", service_name="cartservice", node_name="worker-3"),
        ]

    def execute_disruption(self) -> DisruptionEvent:
        """Simulates scale-up: 3 new pods bin-packed onto worker-1 due to higher capacity."""
        new_pods = [
            PodPlacementRecord(pod_name="payment-3", service_name="paymentservice", node_name="worker-1"),
            PodPlacementRecord(pod_name="payment-4", service_name="paymentservice", node_name="worker-1"),
            PodPlacementRecord(pod_name="payment-5", service_name="paymentservice", node_name="worker-1"),
        ]
        self._active_pods.extend(new_pods)

        return DisruptionEvent(
            event_id="evt-02-scale-up",
            disruption_type=self.disruption_type,
            target_resource="paymentservice",
            parameters={
                "initial_replicas": self._initial_pods_count,
                "scaled_replicas": self._scaled_pods_count,
                "bin_packed_node": "worker-1",
            },
        )

    def rollback(self) -> bool:
        """Restores replica count back to original baseline (2 pods)."""
        self._active_pods = [
            p for p in self._active_pods
            if p.pod_name in {"payment-1", "payment-2", "cart-1"}
        ]
        return True
