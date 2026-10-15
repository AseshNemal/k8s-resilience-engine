import pytest
from experiments.placement_drift.cluster_state_freezer import ClusterStateFreezer
from experiments.placement_drift.constants import ScenarioStatus
from experiments.placement_drift.disruption_orchestrator import DisruptionOrchestrator
from experiments.placement_drift.disruption_reporter import DisruptionReporter
from experiments.placement_drift.placement_drift_calculator import PlacementDriftCalculator
from experiments.placement_drift.scenario_models import PodPlacementRecord


def test_placement_drift_calculator_metrics():
    t0_pods = [
        PodPlacementRecord(pod_name="p1", service_name="svc-a", node_name="node-1"),
        PodPlacementRecord(pod_name="p2", service_name="svc-a", node_name="node-2"),
        PodPlacementRecord(pod_name="p3", service_name="svc-b", node_name="node-2"),
    ]
    t1_pods = [
        # svc-a concentrated onto node-1
        PodPlacementRecord(pod_name="p1", service_name="svc-a", node_name="node-1"),
        PodPlacementRecord(pod_name="p2", service_name="svc-a", node_name="node-1"),
        # svc-b stayed on node-2
        PodPlacementRecord(pod_name="p3", service_name="svc-b", node_name="node-2"),
    ]

    t0_snap = ClusterStateFreezer.freeze("s-t0", t0_pods, [{"name": "node-1"}, {"name": "node-2"}])
    t1_snap = ClusterStateFreezer.freeze("s-t1", t1_pods, [{"name": "node-1"}, {"name": "node-2"}])

    # 1 out of 2 services drifted (svc-a)
    drift_rate = PlacementDriftCalculator.compute_drift_rate(t0_snap, t1_snap)
    assert drift_rate == pytest.approx(0.5)

    drifted_services = PlacementDriftCalculator.get_drifted_services(t0_snap, t1_snap)
    assert drifted_services == ["svc-a"]

    # In t0: svc-a has 2 nodes, svc-b has 1 node -> 1/2 = 0.5 exposure
    # In t1: svc-a has 1 node, svc-b has 1 node -> 2/2 = 1.0 exposure
    e0 = PlacementDriftCalculator.compute_exposure_rate(t0_snap)
    e1 = PlacementDriftCalculator.compute_exposure_rate(t1_snap)
    assert e0 == pytest.approx(0.5)
    assert e1 == pytest.approx(1.0)

    delta_e = PlacementDriftCalculator.compute_delta_exposure_rate(t0_snap, t1_snap)
    assert delta_e == pytest.approx(0.5)


def test_disruption_orchestrator_run_all():
    orchestrator = DisruptionOrchestrator()
    results = orchestrator.run_all()

    assert len(results) == 8
    for r in results:
        assert r.status == ScenarioStatus.COMPLETED
        assert r.rollback_successful is True

    metrics = orchestrator.compute_aggregate_metrics(results)
    assert metrics["total_scenarios"] == 8.0
    assert metrics["completed_count"] == 8.0
    assert metrics["mean_d_rate"] >= 0.0
    assert metrics["total_runtime_ms"] > 0.0


def test_disruption_orchestrator_run_single():
    orchestrator = DisruptionOrchestrator()
    result = orchestrator.run_scenario("02_scale_up_stress")
    assert result is not None
    assert result.scenario_id == "02_scale_up_stress"
    assert result.status == ScenarioStatus.COMPLETED

    none_res = orchestrator.run_scenario("non_existent_id")
    assert none_res is None


def test_disruption_reporter():
    orchestrator = DisruptionOrchestrator()
    results = orchestrator.run_all()
    metrics = orchestrator.compute_aggregate_metrics(results)

    md_report = DisruptionReporter.generate_markdown_report(results, metrics)
    assert "# Component 2: Controlled Chaos & Placement Drift Benchmark Report" in md_report
    assert "01_initial_deployment" in md_report
    assert "07_node_drain_eviction" in md_report

    json_payload = DisruptionReporter.generate_json_payload(results, metrics)
    assert "aggregate_metrics" in json_payload
    assert "scenario_results" in json_payload
    assert len(json_payload["scenario_results"]) == 8
