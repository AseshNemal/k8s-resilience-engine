"""
Abstract Base Class for Phase 4 Controlled Chaos & Disruption Scenarios.
Component: Component 2 - Deployment Topology Modeling
"""
from abc import ABC, abstractmethod
import logging
import time
from typing import Any, Dict, List, Optional

from experiments.placement_drift.cluster_state_freezer import ClusterStateFreezer
from experiments.placement_drift.constants import (
    DEFAULT_CHAOS_NAMESPACE,
    DisruptionType,
    ScenarioExecutionMode,
    ScenarioStatus,
)
from experiments.placement_drift.exceptions import (
    ChaosSafetyError,
    ScenarioExecutionError,
    StateRollbackError,
)
from experiments.placement_drift.safety_guard import SafetyGuard
from experiments.placement_drift.scenario_models import (
    ClusterSnapshot,
    DisruptionEvent,
    NodeTransitionRecord,
    PodPlacementRecord,
    ScenarioResult,
)
from experiments.placement_drift.snapshot_repository import SnapshotRepository
from experiments.placement_drift.placement_drift_calculator import PlacementDriftCalculator


logger = logging.getLogger(__name__)


class BaseScenario(ABC):
    """
    Abstract Base Class defining the formal 4-phase idempotent experiment lifecycle:
    1. prepare(): Captures and freezes baseline state T0.
    2. execute(): Injects controlled disruption (or dry-run simulation).
    3. observe(): Captures post-disruption state T1 and computes D_rate.
    4. rollback(): Restores cluster to baseline state.
    """

    def __init__(
        self,
        scenario_id: str,
        scenario_name: str,
        disruption_type: DisruptionType,
        target_service: str,
        namespace: str = DEFAULT_CHAOS_NAMESPACE,
        execution_mode: ScenarioExecutionMode = ScenarioExecutionMode.DRY_RUN_SIMULATION,
        repository: Optional[SnapshotRepository] = None,
        safety_guard: Optional[SafetyGuard] = None,
    ):
        self.scenario_id = scenario_id
        self.scenario_name = scenario_name
        self.disruption_type = disruption_type
        self.target_service = target_service
        self.namespace = namespace
        self.execution_mode = execution_mode
        self.repository = repository or SnapshotRepository()
        self.safety_guard = safety_guard or SafetyGuard(
            allowed_namespace=namespace,
            execution_mode=execution_mode,
        )
        self.status = ScenarioStatus.PENDING

        # In-memory working state
        self._t0_snapshot: Optional[ClusterSnapshot] = None
        self._t1_snapshot: Optional[ClusterSnapshot] = None
        self._disruption_event: Optional[DisruptionEvent] = None
        self._active_pods: List[PodPlacementRecord] = []
        self._cluster_nodes: List[Dict[str, Any]] = []

    @abstractmethod
    def setup_workload(self) -> None:
        """Initializes the baseline workload and worker node topologies."""
        pass

    @abstractmethod
    def execute_disruption(self) -> DisruptionEvent:
        """Injects the specific disruption fault into the workload or node."""
        pass

    def prepare(self) -> ClusterSnapshot:
        """Executes pre-flight checks and freezes baseline state T0."""
        self.status = ScenarioStatus.PREPARING
        self.safety_guard.validate_namespace_safety(self.namespace)
        self.setup_workload()
        self.safety_guard.validate_preflight_nodes(self._cluster_nodes)
        self.safety_guard.validate_workload_presence(self._active_pods, self.target_service)

        t0_id = f"{self.scenario_id}_T0"
        self._t0_snapshot = ClusterStateFreezer.freeze(
            snapshot_id=t0_id,
            raw_pods=self._active_pods,
            nodes=self._cluster_nodes,
            namespace=self.namespace,
        )
        self.repository.save(self.scenario_id, "T0", self._t0_snapshot)
        logger.info("[%s] Captured T0 snapshot: %s (hash: %s)", self.scenario_id, t0_id, self._t0_snapshot.sha256_hash[:8])
        return self._t0_snapshot

    def observe(self) -> ClusterSnapshot:
        """Freezes post-disruption state T1 and registers it in the repository."""
        self.status = ScenarioStatus.OBSERVING
        t1_id = f"{self.scenario_id}_T1"
        self._t1_snapshot = ClusterStateFreezer.freeze(
            snapshot_id=t1_id,
            raw_pods=self._active_pods,
            nodes=self._cluster_nodes,
            namespace=self.namespace,
        )
        self.repository.save(self.scenario_id, "T1", self._t1_snapshot)
        logger.info("[%s] Captured T1 snapshot: %s (hash: %s)", self.scenario_id, t1_id, self._t1_snapshot.sha256_hash[:8])
        return self._t1_snapshot

    @abstractmethod
    def rollback(self) -> bool:
        """Idempotently restores the cluster/workload to its initial healthy state."""
        pass

    def _compute_node_transitions(
        self, t0: ClusterSnapshot, t1: ClusterSnapshot
    ) -> List[NodeTransitionRecord]:
        """Calculates pod migration counts from node n_i to node n_j."""
        t0_map = {p.pod_name: p for p in t0.pods}
        t1_map = {p.pod_name: p for p in t1.pods}

        transitions_map: Dict[tuple[str, str], List[str]] = {}
        for pod_name, p0 in t0_map.items():
            if pod_name in t1_map:
                p1 = t1_map[pod_name]
                if p0.node_name != p1.node_name:
                    pair = (p0.node_name, p1.node_name)
                    if pair not in transitions_map:
                        transitions_map[pair] = []
                    transitions_map[pair].append(p0.service_name)

        return [
            NodeTransitionRecord(
                from_node=src,
                to_node=dst,
                pod_count=len(svcs),
                services_affected=sorted(set(svcs)),
            )
            for (src, dst), svcs in sorted(transitions_map.items())
        ]

    def run(self) -> ScenarioResult:
        """
        Template method orchestrating the complete scenario lifecycle.
        Guarantees rollback execution even if disruption observation raises an error.
        """
        start_time = time.perf_counter()
        rollback_done = False
        error_msg: Optional[str] = None

        try:
            t0 = self.prepare()
            self.status = ScenarioStatus.EXECUTING
            self._disruption_event = self.execute_disruption()
            t1 = self.observe()

            # Formal metrics using Phase 4 PlacementDriftCalculator
            d_rate = PlacementDriftCalculator.compute_drift_rate(t0, t1)
            delta_e_rate = PlacementDriftCalculator.compute_delta_exposure_rate(t0, t1)
            drifted_services = PlacementDriftCalculator.get_drifted_services(t0, t1)
            transitions = self._compute_node_transitions(t0, t1)

            self.status = ScenarioStatus.ROLLING_BACK
            rollback_done = self.rollback()
            self.status = ScenarioStatus.COMPLETED

            elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
            return ScenarioResult(
                scenario_id=self.scenario_id,
                scenario_name=self.scenario_name,
                disruption_type=self.disruption_type,
                status=self.status,
                execution_mode=self.execution_mode,
                baseline_snapshot_id=t0.snapshot_id,
                target_snapshot_id=t1.snapshot_id,
                baseline_hash=t0.sha256_hash,
                target_hash=t1.sha256_hash,
                d_rate=d_rate,
                delta_e_rate=delta_e_rate,
                drifted_services=drifted_services,
                transitions=transitions,
                runtime_ms=elapsed_ms,
                rollback_successful=rollback_done,
            )


        except ChaosSafetyError as exc:
            self.status = ScenarioStatus.ABORTED_BY_SAFETY
            error_msg = str(exc)
            logger.exception("[%s] Safety Guard Abort: %s", self.scenario_id, exc)
        except Exception as exc:
            self.status = ScenarioStatus.FAILED
            error_msg = str(exc)
            logger.exception("[%s] Scenario Execution Failed: %s", self.scenario_id, exc)
            try:
                rollback_done = self.rollback()
            except Exception as rb_exc:
                logger.exception("[%s] Rollback Failed: %s", self.scenario_id, rb_exc)

        elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
        return ScenarioResult(
            scenario_id=self.scenario_id,
            scenario_name=self.scenario_name,
            disruption_type=self.disruption_type,
            status=self.status,
            execution_mode=self.execution_mode,
            baseline_snapshot_id=f"{self.scenario_id}_T0",
            target_snapshot_id=f"{self.scenario_id}_T1",
            runtime_ms=elapsed_ms,
            error_message=error_msg,
            rollback_successful=rollback_done,
        )
