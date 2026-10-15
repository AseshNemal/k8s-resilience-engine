import pytest
from pydantic import ValidationError
from experiments.placement_drift.constants import DisruptionType, ScenarioStatus, ScenarioExecutionMode
from experiments.placement_drift.scenario_models import (
    PodPlacementRecord,
    ClusterSnapshot,
    DisruptionEvent,
    NodeTransitionRecord,
    ScenarioResult,
)


def test_pod_placement_record_valid():
    record = PodPlacementRecord(
        pod_name="frontend-abc-123",
        service_name="frontend",
        namespace="chaos-resilience-study",
        node_name="worker-node-1",
        zone="zone-a",
        is_ready=True,
    )
    assert record.pod_name == "frontend-abc-123"
    assert record.service_name == "frontend"
    assert record.node_name == "worker-node-1"
    assert record.zone == "zone-a"
    assert record.is_ready is True


def test_cluster_snapshot_creation():
    p1 = PodPlacementRecord(
        pod_name="p1",
        service_name="svc-a",
        namespace="chaos-resilience-study",
        node_name="node-1",
    )
    p2 = PodPlacementRecord(
        pod_name="p2",
        service_name="svc-b",
        namespace="chaos-resilience-study",
        node_name="node-2",
    )
    snapshot = ClusterSnapshot(
        snapshot_id="snap-t0",
        sha256_hash="dummy-sha256",
        namespace="chaos-resilience-study",
        pods=[p1, p2],
        nodes=[{"name": "node-1"}, {"name": "node-2"}],
        total_pods=2,
        total_services=2,
    )
    assert snapshot.snapshot_id == "snap-t0"
    assert len(snapshot.pods) == 2
    assert snapshot.nodes[0]["name"] == "node-1"
    assert snapshot.sha256_hash == "dummy-sha256"


def test_disruption_event_valid():
    event = DisruptionEvent(
        event_id="evt-1",
        disruption_type=DisruptionType.POD_DELETION_CHAOS,
        target_resource="checkout-pod-xyz",
        parameters={"count": 1},
    )
    assert event.event_id == "evt-1"
    assert event.disruption_type == DisruptionType.POD_DELETION_CHAOS
    assert event.target_resource == "checkout-pod-xyz"
    assert event.parameters["count"] == 1


def test_node_transition_record():
    trans = NodeTransitionRecord(
        from_node="node-1",
        to_node="node-2",
        pod_count=3,
        services_affected=["svc-a", "svc-b"],
    )
    assert trans.from_node == "node-1"
    assert trans.to_node == "node-2"
    assert trans.pod_count == 3
    assert len(trans.services_affected) == 2


def test_scenario_result_model():
    res = ScenarioResult(
        scenario_id="05_pod_deletion_chaos",
        scenario_name="Pod Deletion Chaos",
        disruption_type=DisruptionType.POD_DELETION_CHAOS,
        status=ScenarioStatus.COMPLETED,
        execution_mode=ScenarioExecutionMode.DRY_RUN_SIMULATION,
        baseline_snapshot_id="snap-t0",
        target_snapshot_id="snap-t1",
        baseline_hash="hash-t0",
        target_hash="hash-t1",
        d_rate=0.5,
        delta_e_rate=0.25,
        drifted_services=["frontend"],
        transitions=[
            NodeTransitionRecord(
                from_node="node-1",
                to_node="node-2",
                pod_count=1,
                services_affected=["frontend"],
            )
        ],
        rollback_successful=True,
        runtime_ms=125.4,
    )
    assert res.scenario_id == "05_pod_deletion_chaos"
    assert res.status == ScenarioStatus.COMPLETED
    assert res.d_rate == pytest.approx(0.5)
    assert res.delta_e_rate == pytest.approx(0.25)
    assert res.rollback_successful is True
