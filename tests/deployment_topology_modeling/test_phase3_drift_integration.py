"""End-to-end checks on the stored KinD snapshot (experiments/single-node-failure/concentrated-baseline).

Expected values are read from the kubectl output in that folder:
- pod-placement.txt: 3 paymentservice pods, all Running/Ready on resilience-research-worker
- paymentservice-deployment.yaml: replicas 3, nodeSelector research-node=worker-1
- worker1-failure/nodes-after-detection.txt: resilience-research-worker NotReady,
  service-result.txt: SERVICE_UNAVAILABLE
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.engine.deployment_topology_modeling import service
from app.engine.deployment_topology_modeling.placement_metrics import ExposureStatus
from app.engine.deployment_topology_modeling.drift_analysis.drift_audit_trail import DriftAuditTrail
from app.engine.deployment_topology_modeling.drift_analysis.placement_drift_report import PlacementDriftReport
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    DriftViolation,
    ClusterExposureReport,
    BaselineB2Result,
)
from app.engine.deployment_topology_modeling.drift_analysis.constants import DriftType, DriftSeverity
from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import nodes_from_objects
from tests.deployment_topology_modeling import kind_snapshot

NODE = kind_snapshot.CONCENTRATED_NODE


def test_drift_audit_trail():
    trail = DriftAuditTrail()
    trail.record_run("test-cluster", 10, 2, 0.20, 0.90)
    trail.record_violation("cart", DriftViolation(
        drift_type=DriftType.SCALE_DRIFT, severity=DriftSeverity.HIGH,
        description="Replica count mismatch", expected=3, actual=1,
    ))
    records = trail.get_records()
    assert [r["event_type"] for r in records] == ["DRIFT_ANALYSIS_RUN", "VIOLATION_DETECTED"]
    assert trail.get_records(service_name="cart")[0]["severity"] == "HIGH"


def test_placement_drift_report_markdown_handles_unmeasurable_rates():
    report = ClusterExposureReport(timestamp="t", total_services=0, evaluated_services=0, exposed_service_count=0)
    md = PlacementDriftReport.generate_markdown_summary(report, BaselineB2Result())
    assert "not measurable" in md


def test_live_graph_from_real_snapshot():
    graph = service.get_live_deployment_graph()
    assert graph.cluster.node_names == [
        "resilience-research-worker", "resilience-research-worker2", "resilience-research-worker3",
    ]  # control-plane excluded
    (pay,) = graph.services
    assert pay.name == "paymentservice"
    assert pay.deployment_names == ["paymentservice"]
    assert pay.declared_replicas == 3
    assert pay.ready_replicas == 3
    assert pay.concentrated is True
    assert pay.replica_concentration == pytest.approx(1.0)
    assert pay.exposed_failure_domains == [NODE]
    assert {p.node for p in pay.pods} == {NODE}
    assert {p.phase for p in pay.pods} == {"Running"}
    # Declared intent (deploy/test-workloads) is associated through the resolved Deployment:
    assert pay.declared_intent is not None
    assert pay.declared_intent.node_selector == {"research-node": "worker-1"}
    # replicas 3 declared / 3 ready, nodeSelector satisfied -> no intent drift.
    assert pay.drift_detected is False


def test_snapshot_id_is_reproducible():
    assert service.get_live_deployment_graph().snapshot_id == service.get_live_deployment_graph().snapshot_id


def test_exposure_report_from_real_snapshot():
    report, b2 = service.analyze_resilience_drift()
    assert report.total_services == 1
    assert report.exposed_services == ["paymentservice"]
    assert report.exposure_rate == pytest.approx(1.0)          # 1 exposed / 1 evaluated
    assert report.services_with_placement_rules == 1           # nodeSelector
    assert report.constraint_compliance_rate == pytest.approx(1.0)  # all pods on research-node=worker-1
    assert b2.most_critical_worker_node == NODE
    assert b2.exposed_services_by_node == {NODE: ["paymentservice"]}


def test_pods_on_not_ready_node_are_not_counted_as_ready(monkeypatch):
    """After worker-1 failed, kubectl still showed the pods 1/1 Running (worker1-failure/pods.txt)
    while the node was NotReady. The model must not count them as surviving replicas."""
    nodes_json = kind_snapshot.node_objects()
    for node in nodes_json:
        if node["metadata"]["name"] == NODE:
            for cond in node["status"]["conditions"]:
                if cond["type"] == "Ready":
                    cond["status"] = "Unknown"

    def collect_with_failed_node(namespace="default"):
        _, analyzed_ok = kind_snapshot.collect()
        nodes = nodes_from_objects(nodes_json, kind_snapshot.pod_objects())
        from app.engine.deployment_topology_modeling.live_state.concentration_analyzer import (
            analyze_replica_concentration,
        )
        workloads = {s["name"]: {**s, "pods": [{**p, "ready": p["readyCondition"]} for p in s["pods"]]}
                     for s in analyzed_ok}
        return nodes, analyze_replica_concentration(workloads, {n.name: n.ready for n in nodes})

    monkeypatch.setattr(service, "collect_live_cluster", collect_with_failed_node)
    report, _ = service.analyze_resilience_drift()
    (pay,) = report.services
    assert pay.ready_replicas == 0
    assert pay.exposure_status == ExposureStatus.UNAVAILABLE  # matches observed SERVICE_UNAVAILABLE
    assert report.unavailable_services == ["paymentservice"]
    assert report.exposure_rate is None                        # nothing left to evaluate


def test_cluster_unavailable_is_an_error_not_sample_data(monkeypatch):
    def no_cluster(namespace="default"):
        raise service.ClusterUnavailableError("no kubeconfig")

    monkeypatch.setattr(service, "collect_live_cluster", no_cluster)
    with pytest.raises(service.ClusterUnavailableError):
        service.get_live_deployment_graph()

    from app.main import app
    resp = TestClient(app).get("/api/deployments/live")
    assert resp.status_code == 503
    assert resp.json()["data_source"] == "live"


def test_engine_endpoints_drift_and_baseline():
    from app.engine.deployment_topology_modeling.endpoints import router as engine_router
    test_app = FastAPI()
    test_app.include_router(engine_router)
    client = TestClient(test_app)

    data_drift = client.get("/engine/deployment-topology/drift").json()
    assert data_drift["exposure_rate"] == pytest.approx(1.0)
    data_b2 = client.get("/engine/deployment-topology/baseline-b2").json()
    assert data_b2["total_single_points_of_failure"] == 1

    report = service.analyze_resilience_drift()[0].model_dump(mode="json")
    resp = client.post("/engine/deployment-topology/diff", json={"baseline": report, "target": report})
    assert resp.status_code == 200
    assert resp.json()["placement_drift_rate"] == pytest.approx(0.0)
