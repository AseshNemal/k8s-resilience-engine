"""
Scenario 05: Involuntary Pod Deletion & Reschedule Drift Scenario.
Component: Component 2 - Deployment Topology Modeling
Simulates sudden pod eviction/crash and verifies whether replacement pod reschedules into a concentrated domain.
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


class Scenario05PodDeletionChaos(BaseScenario):
    """
    Scenario 05: Involuntary Pod Deletion & Reschedule Drift.
    Simulates pod crash (e.g. OOMKill) and tests if replacement pod concentrates onto worker-1.
    """

    def __init__(
        self,
        namespace: str = DEFAULT_CHAOS_NAMESPACE,
        execution_mode: ScenarioExecutionMode = ScenarioExecutionMode.DRY_RUN_SIMULATION,
        **kwargs: Any,
    ):
        super().__init__(
            scenario_id="05_pod_deletion_chaos",
            scenario_name="Involuntary Pod Deletion & Reschedule Drift",
            disruption_type=DisruptionType.POD_DELETION_CHAOS,
            target_service="paymentservice",
            namespace=namespace,
            execution_mode=execution_mode,
            **kwargs,
        )

    def setup_workload(self) -> None:
        """Sets up payment-1 on worker-1 and payment-2 on worker-2."""
        self._cluster_nodes = [
            {"name": "worker-1", "zone": "us-central1-a", "cpu": "4", "memory": "8Gi"},
            {"name": "worker-2", "zone": "us-central1-b", "cpu": "4", "memory": "8Gi"},
            {"name": "worker-3", "zone": "us-central1-c", "cpu": "4", "memory": "8Gi"},
        ]
        self._active_pods = [
            PodPlacementRecord(pod_name="payment-1", service_name="paymentservice", node_name="worker-1"),
            PodPlacementRecord(pod_name="payment-2", service_name="paymentservice", node_name="worker-2"),
            PodPlacementRecord(pod_name="email-1", service_name="emailservice", node_name="worker-3"),
        ]

    def execute_disruption(self) -> DisruptionEvent:
        """Deletes payment-2 from worker-2; replacement payment-3 is scheduled onto worker-1."""
        self._active_pods = [
            p for p in self._active_pods if p.pod_name != "payment-2"
        ]
        # Replacement pod created by ReplicaSet controller
        self._active_pods.append(
            PodPlacementRecord(pod_name="payment-3", service_name="paymentservice", node_name="worker-1")
        )

        return DisruptionEvent(
            event_id="evt-05-pod-delete",
            disruption_type=self.disruption_type,
            target_resource="payment-2",
            parameters={
                "deleted_pod": "payment-2",
                "replacement_pod": "payment-3",
                "rescheduled_node": "worker-1",
            },
        )

    def rollback(self) -> bool:
        """Restores baseline spread (payment-1 on worker-1, payment-2 on worker-2)."""
        self.setup_workload()
        return True
