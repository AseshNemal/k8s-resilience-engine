import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.engine.deployment_topology_modeling.service import (
    get_live_deployment_graph,
    analyze_resilience_drift,
    diff_cluster_snapshots,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_audit_trail import DriftAuditTrail
from app.engine.deployment_topology_modeling.drift_analysis.placement_drift_report import PlacementDriftReport
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    DriftViolation,
    ClusterExposureReport,
    BaselineB2Result,
)
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftType,
    DriftSeverity,
    ExposureLevel,
)


def test_drift_audit_trail():
    trail = DriftAuditTrail()
    trail.record_run("test-cluster", 10, 2, 0.20, 0.90)
    v = DriftViolation(
        drift_type=DriftType.SCALE_DRIFT,
        severity=DriftSeverity.HIGH,
        description="Replica count mismatch",
        expected="3 replicas",
        actual="1 ready replica",
    )
    trail.record_violation("cart", v)
    records = trail.get_records()
    assert len(records) == 2
    assert records[0]["event_type"] == "DRIFT_ANALYSIS_RUN"
    assert records[1]["event_type"] == "VIOLATION_DETECTED"
    assert records[1]["service_name"] == "cart"

    cart_only = trail.get_records(service_name="cart")
    assert len(cart_only) == 1
    assert cart_only[0]["severity"] == "HIGH"


def test_placement_drift_report_markdown():
    exp = ClusterExposureReport(
        timestamp="2026-10-11T00:00:00Z",
        total_services=2,
        exposed_service_count=1,
        exposure_rate=0.5,
        constraint_compliance_rate=0.5,
        mean_concentration=0.75,
        runtime_overhead_ms=12.5,
        services=[],
    )
    b2 = BaselineB2Result(
        service_rankings=[
            {
                "rank": 1,
                "service_name": "payment",
                "exposure_level": ExposureLevel.FULL_EXPOSURE.value,
                "severity_score": 0.85,
                "replica_concentration": 1.0,
                "ready_replicas": 2,
                "exposed_nodes": ["node-1"],
            }
        ],
        most_vulnerable_service="payment",
        most_critical_worker_node="node-1",
        total_single_points_of_failure=1,
    )
    md = PlacementDriftReport.generate_markdown_summary(exp, b2)
    assert "# Kubernetes Deployment Topology Resilience & Drift Report" in md
    assert "**Exposed Services (Single Point of Failure):** 1 (50.0%)" in md
    assert "payment" in md
    assert "node-1" in md


from tests.support.fake_cluster import cluster_from_graph
from app.engine.deployment_topology_modeling.service import get_sample_deployment


def test_service_live_deployment_graph_integration(use_cluster):
    use_cluster(cluster_from_graph(get_sample_deployment()))
    graph = get_live_deployment_graph()
    assert graph.nodes is not None
    assert graph.services is not None
    assert graph.validation.runtime_overhead_ms >= 0.0
    # Every service must have drift_detected boolean
    for svc in graph.services:
        assert isinstance(svc.drift_detected, bool)
        assert isinstance(svc.drift_reasons, list)


def test_service_analyze_resilience_drift_facade(use_cluster):
    use_cluster(cluster_from_graph(get_sample_deployment()))
    cluster_report, b2 = analyze_resilience_drift()
    assert isinstance(cluster_report, ClusterExposureReport)
    assert isinstance(b2, BaselineB2Result)
    assert cluster_report.total_services >= 0
    assert 0.0 <= cluster_report.exposure_rate <= 1.0
    assert cluster_report.constraint_compliance_rate is None  # no declared-state source selected
    assert cluster_report.declared_state == "unavailable"


from fastapi import FastAPI
from app.engine.deployment_topology_modeling.endpoints import router as engine_router


def test_engine_endpoints_drift_and_baseline(use_cluster):
    use_cluster(cluster_from_graph(get_sample_deployment()))
    test_app = FastAPI()
    test_app.include_router(engine_router)
    client = TestClient(test_app)
    
    # Test /engine/deployment-topology/drift
    resp_drift = client.get("/engine/deployment-topology/drift")
    assert resp_drift.status_code == 200
    data_drift = resp_drift.json()
    assert "exposure_rate" in data_drift
    assert "constraint_compliance_rate" in data_drift
    assert "services" in data_drift

    # Test /engine/deployment-topology/baseline-b2
    resp_b2 = client.get("/engine/deployment-topology/baseline-b2")
    assert resp_b2.status_code == 200
    data_b2 = resp_b2.json()
    assert "service_rankings" in data_b2
    assert "total_single_points_of_failure" in data_b2

    # Test /engine/deployment-topology/diff
    diff_payload = {
        "baseline": {
            "services": [
                {
                    "service_name": "cart",
                    "total_replicas": 2,
                    "ready_replicas": 2,
                    "replica_concentration": 0.5,
                    "exposure_level": "RESILIENT",
                    "severity_score": 0.0,
                    "violations": [],
                }
            ]
        },
        "target": {
            "services": [
                {
                    "service_name": "cart",
                    "total_replicas": 2,
                    "ready_replicas": 1,
                    "replica_concentration": 1.0,
                    "exposure_level": "FULL_EXPOSURE",
                    "severity_score": 0.8,
                    "violations": [
                        {
                            "drift_type": "SCALE_DRIFT",
                            "severity": "HIGH",
                            "description": "Replica lost",
                        }
                    ],
                }
            ]
        },
    }
    resp_diff = client.post("/engine/deployment-topology/diff", json=diff_payload)
    assert resp_diff.status_code == 200
    data_diff = resp_diff.json()
    assert data_diff["placement_drift_rate"] == 1.0
    assert len(data_diff["state_transitions"]) == 1
