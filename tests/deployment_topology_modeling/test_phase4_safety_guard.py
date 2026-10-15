import pytest
from experiments.placement_drift.constants import ScenarioExecutionMode
from experiments.placement_drift.exceptions import ChaosSafetyError
from experiments.placement_drift.safety_guard import SafetyGuard
from experiments.placement_drift.scenario_models import PodPlacementRecord


def test_safety_guard_rejects_empty_namespace():
    guard = SafetyGuard()
    with pytest.raises(ChaosSafetyError, match="cannot be empty or null"):
        guard.validate_namespace_safety("")


def test_safety_guard_blocks_kube_system():
    guard = SafetyGuard()
    with pytest.raises(ChaosSafetyError, match="is protected"):
        guard.validate_namespace_safety("kube-system")


def test_safety_guard_blocks_kube_public_and_node_lease():
    guard = SafetyGuard()
    with pytest.raises(ChaosSafetyError, match="is protected"):
        guard.validate_namespace_safety("kube-public")
    with pytest.raises(ChaosSafetyError, match="is protected"):
        guard.validate_namespace_safety("kube-node-lease")


def test_safety_guard_allows_custom_and_default_with_flag():
    guard = SafetyGuard(allow_default_namespace=True)
    guard.validate_namespace_safety("default")
    guard.validate_namespace_safety("chaos-resilience-study")

    guard_strict = SafetyGuard(allow_default_namespace=False)
    with pytest.raises(ChaosSafetyError, match="is protected"):
        guard_strict.validate_namespace_safety("default")


def test_validate_preflight_nodes_rejects_empty():
    guard = SafetyGuard()
    with pytest.raises(ChaosSafetyError, match="No worker nodes available"):
        guard.validate_preflight_nodes([])


def test_validate_preflight_nodes_accepts_valid_nodes():
    guard = SafetyGuard()
    guard.validate_preflight_nodes([{"name": "node-1"}, {"name": "node-2"}])


def test_validate_workload_presence():
    guard = SafetyGuard()
    pod = PodPlacementRecord(
        pod_name="frontend-1",
        service_name="frontend",
        namespace="chaos",
        node_name="node-1",
    )
    guard.validate_workload_presence([pod], "frontend")

    with pytest.raises(ChaosSafetyError, match="No active pods found"):
        guard.validate_workload_presence([pod], "non-existent-svc")


def test_can_execute_destructive_action():
    guard_dry = SafetyGuard(execution_mode=ScenarioExecutionMode.DRY_RUN_SIMULATION)
    assert guard_dry.can_execute_destructive_action("delete_pod") is False

    guard_live = SafetyGuard(execution_mode=ScenarioExecutionMode.LIVE_CLUSTER)
    assert guard_live.can_execute_destructive_action("delete_pod") is True
