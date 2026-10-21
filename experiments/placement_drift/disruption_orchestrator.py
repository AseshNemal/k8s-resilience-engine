"""
Disruption Orchestrator: Runs multi-scenario chaos experiment pipelines and compiles benchmark results.
Component: Component 2 - Deployment Topology Modeling
"""
import logging
import time
from typing import Any, Dict, List, Optional, Type

from experiments.placement_drift.base_scenario import BaseScenario
from experiments.placement_drift.constants import (
    DEFAULT_CHAOS_NAMESPACE,
    ScenarioExecutionMode,
    ScenarioStatus,
)
from experiments.placement_drift.scenario_models import ScenarioResult
from experiments.placement_drift.snapshot_repository import SnapshotRepository
from experiments.placement_drift.scenario_01_initial_deployment import Scenario01InitialDeployment
from experiments.placement_drift.scenario_02_scale_up_stress import Scenario02ScaleUpStress
from experiments.placement_drift.scenario_03_scale_down_loss import Scenario03ScaleDownLoss
from experiments.placement_drift.scenario_04_rolling_update import Scenario04RollingUpdate
from experiments.placement_drift.scenario_05_pod_deletion_chaos import Scenario05PodDeletionChaos
from experiments.placement_drift.scenario_06_node_cordon import Scenario06NodeCordon
from experiments.placement_drift.scenario_07_node_drain_eviction import Scenario07NodeDrainEviction
from experiments.placement_drift.scenario_08_resource_contention import Scenario08ResourceContention

logger = logging.getLogger(__name__)


class DisruptionOrchestrator:
    """
    Coordinates execution of all 8 Section 10 disruption scenarios.
    Collects results into comprehensive benchmark summaries.
    """

    ALL_SCENARIO_CLASSES: List[Type[BaseScenario]] = [
        Scenario01InitialDeployment,
        Scenario02ScaleUpStress,
        Scenario03ScaleDownLoss,
        Scenario04RollingUpdate,
        Scenario05PodDeletionChaos,
        Scenario06NodeCordon,
        Scenario07NodeDrainEviction,
        Scenario08ResourceContention,
    ]

    def __init__(
        self,
        namespace: str = DEFAULT_CHAOS_NAMESPACE,
        execution_mode: ScenarioExecutionMode = ScenarioExecutionMode.DRY_RUN_SIMULATION,
        repository: Optional[SnapshotRepository] = None,
        topology: Optional[Any] = None,
    ):
        """``topology`` is a real DeploymentGraph. Given one, every scenario starts from its real nodes and
        pods (see live_topology_scenarios.py); without one the scripted placements are used."""
        self.namespace = namespace
        self.execution_mode = execution_mode
        self.repository = repository or SnapshotRepository()
        self.topology = topology

    def _instances(self) -> List[BaseScenario]:
        scripted = [
            cls(namespace=self.namespace, execution_mode=self.execution_mode, repository=self.repository)
            for cls in self.ALL_SCENARIO_CLASSES
        ]
        if self.topology is None:
            return scripted
        from experiments.placement_drift.live_topology_scenarios import LiveTopologyScenario

        return [
            LiveTopologyScenario(template, self.topology, self.namespace, self.execution_mode, self.repository)
            for template in scripted
        ]

    def run_all(self) -> List[ScenarioResult]:
        """Executes all 8 scenarios sequentially."""
        results: List[ScenarioResult] = []
        for instance in self._instances():
            result = instance.run()
            results.append(result)
            logger.info(
                "Completed [%s] status=%s d_rate=%s",
                result.scenario_id,
                result.status.value,
                result.d_rate,
            )
        return results

    def run_scenario(self, scenario_id: str) -> Optional[ScenarioResult]:
        """Executes a single specified scenario by ID."""
        for instance in self._instances():
            if instance.scenario_id == scenario_id:
                return instance.run()
        return None

    def compute_aggregate_metrics(
        self, results: List[ScenarioResult]
    ) -> Dict[str, Any]:
        """Aggregates completed scenarios; D_rate statistics are None when none were measurable."""
        completed = [r for r in results if r.status == ScenarioStatus.COMPLETED]
        d_rates = [r.d_rate for r in completed if r.d_rate is not None]
        return {
            "total_scenarios": float(len(results)),
            "completed_count": float(len(completed)),
            "execution_mode": self.execution_mode.value,
            "topology_source": "live" if self.topology is not None else "scripted",
            "is_experimental_evidence": self.execution_mode == ScenarioExecutionMode.LIVE_CLUSTER,
            "mean_d_rate": round(sum(d_rates) / len(d_rates), 4) if d_rates else None,
            "max_d_rate": round(max(d_rates), 4) if d_rates else None,
            "total_runtime_ms": round(sum(r.runtime_ms for r in completed), 2),
        }
