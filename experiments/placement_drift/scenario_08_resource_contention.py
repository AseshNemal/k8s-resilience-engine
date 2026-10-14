"""
Scenario 08: Constrained Capacity Bin-Packing Scenario.
Component: Component 2 - Deployment Topology Modeling
Simulates node resource exhaustion forcing the scheduler to pack pods onto remaining capacity.
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


class Scenario08ResourceContention(BaseScenario):
    """
    Scenario 08: Constrained Capacity Bin-Packing.
    Simulates memory/CPU exhaustion on worker-3, forcing new payment pods to co-locate on worker-1.
    """

    def __init__(
        self,
        namespace: str = DEFAULT_CHAOS_NAMESPACE,
        execution_mode: ScenarioExecutionMode = ScenarioExecutionMode.DRY_RUN_SIMULATION,
        **kwargs: Any,
    ):
        super().__init__(
            scenario_id="08_resource_contention",
            scenario_name="Constrained Capacity Bin-Packing",
            disruption_type=DisruptionType.RESOURCE_CONTENTION,
            target_service="paymentservice",
            namespace=namespace,
            execution_mode=execution_mode,
            **kwargs,
        )

    def setup_workload(self) -> None:
        """Sets up 3 worker nodes, with worker-3 initially available."""
        self._cluster_nodes = [
            {"name": "worker-1", "zone": "us-central1-a", "cpu_available": "2.0", "mem_available_mb": 4096},
            {"name": "worker-2", "zone": "us-central1-b", "cpu_available": "2.0", "mem_available_mb": 4096},
            {"name": "worker-3", "zone": "us-central1-c", "cpu_available": "2.0", "mem_available_mb": 4096},
        ]
        self._active_pods = [
            PodPlacementRecord(pod_name="payment-1", service_name="paymentservice", node_name="worker-1"),
            PodPlacementRecord(pod_name="payment-2", service_name="paymentservice", node_name="worker-2"),
            PodPlacementRecord(pod_name="cart-1", service_name="cartservice", node_name="worker-3"),
        ]

    def execute_disruption(self) -> DisruptionEvent:
        """Simulates memory exhaustion on worker-2 and worker-3; scales payment forcing all onto worker-1."""
        for n in self._cluster_nodes:
            if n["name"] in {"worker-2", "worker-3"}:
                n["cpu_available"] = "0.05"
                n["mem_available_mb"] = 32

        # Scheduler has only worker-1 with capacity -> payment-3 and payment-4 land on worker-1
        self._active_pods.extend([
            PodPlacementRecord(pod_name="payment-3", service_name="paymentservice", node_name="worker-1"),
            PodPlacementRecord(pod_name="payment-4", service_name="paymentservice", node_name="worker-1"),
        ])

        return DisruptionEvent(
            event_id="evt-08-resource-exhaust",
            disruption_type=self.disruption_type,
            target_resource="worker-2,worker-3",
            parameters={
                "exhausted_nodes": ["worker-2", "worker-3"],
                "remaining_node": "worker-1",
                "co_located_pods": ["payment-1", "payment-3", "payment-4"],
            },
        )

    def rollback(self) -> bool:
        """Restores allocatable capacity and resets baseline pods."""
        self.setup_workload()
        return True
