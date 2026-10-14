import pytest
from experiments.placement_drift.base_scenario import BaseScenario
from experiments.placement_drift.constants import (
    DisruptionType,
    ScenarioExecutionMode,
    ScenarioStatus,
)
from experiments.placement_drift.exceptions import ChaosSafetyError
from experiments.placement_drift.scenario_models import (
    ClusterSnapshot,
    DisruptionEvent,
    PodPlacementRecord,
)


class DummyTestScenario(BaseScenario):
    """Concrete scenario for testing base template lifecycle."""

    def __init__(self, should_fail_execute: bool = False, **kwargs):
        super().__init__(
            scenario_id="99_dummy_scenario",
            scenario_name="Dummy Test Scenario",
            disruption_type=DisruptionType.INITIAL_DEPLOYMENT,
            target_service="test-svc",
            **kwargs,
        )
        self.should_fail_execute = should_fail_execute
        self.setup_called = False
        self.rollback_called = False

    def setup_workload(self) -> None:
        self.setup_called = True
        self._cluster_nodes = [
            {"name": "worker-1", "zone": "zone-a"},
            {"name": "worker-2", "zone": "zone-b"},
        ]
        self._active_pods = [
            PodPlacementRecord(
                pod_name="pod-1",
                service_name="test-svc",
                namespace=self.namespace,
                node_name="worker-1",
            ),
            PodPlacementRecord(
                pod_name="pod-2",
                service_name="test-svc",
                namespace=self.namespace,
                node_name="worker-2",
            ),
        ]

    def execute_disruption(self) -> DisruptionEvent:
        if self.should_fail_execute:
            raise RuntimeError("Injected execution failure")
        # Shift pod-1 to worker-2
        self._active_pods[0].node_name = "worker-2"
        return DisruptionEvent(
            event_id="dummy-evt",
            disruption_type=self.disruption_type,
            target_resource="test-svc",
        )

    def rollback(self) -> bool:
        self.rollback_called = True
        if self._active_pods:
            self._active_pods[0].node_name = "worker-1"
        return True


def test_base_scenario_lifecycle_success():
    scenario = DummyTestScenario()
    res = scenario.run()

    assert scenario.setup_called is True
    assert scenario.rollback_called is True
    assert res.status == ScenarioStatus.COMPLETED
    assert res.rollback_successful is True
    assert res.scenario_id == "99_dummy_scenario"
    assert res.runtime_ms >= 0.0
    assert len(res.transitions) == 1
    assert res.transitions[0].from_node == "worker-1"
    assert res.transitions[0].to_node == "worker-2"
    assert res.transitions[0].pod_count == 1


def test_base_scenario_aborted_by_safety():
    scenario = DummyTestScenario(namespace="kube-system")
    res = scenario.run()

    assert res.status == ScenarioStatus.ABORTED_BY_SAFETY
    assert "is protected" in (res.error_message or "")
    assert res.rollback_successful is False


def test_base_scenario_execution_failure_triggers_rollback():
    scenario = DummyTestScenario(should_fail_execute=True)
    res = scenario.run()

    assert res.status == ScenarioStatus.FAILED
    assert "Injected execution failure" in (res.error_message or "")
    assert scenario.rollback_called is True
    assert res.rollback_successful is True
