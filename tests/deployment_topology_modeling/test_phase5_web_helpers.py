"""
Tests for Phase 5 Web Helpers: DashboardContextBuilder and AcademicMetricPresenter.
Component: Component 2 - Deployment Topology Modeling
"""
import pytest
from app.engine.deployment_topology_modeling.web.academic_metric_presenter import (
    AcademicMetricPresenter,
)
from app.engine.deployment_topology_modeling.web.dashboard_context_builder import (
    DashboardContextBuilder,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    ModelAccuracyCertificate,
)


def test_academic_metric_presenter_passing_accuracy():
    badge = AcademicMetricPresenter.format_accuracy_badge(0.99, target=0.98)
    assert badge["is_passing"] is True
    assert badge["status_label"] == "PASSED"
    assert badge["display"] == "99.0%"
    assert badge["css_class"] == "badge--success"


def test_academic_metric_presenter_degraded_accuracy():
    badge = AcademicMetricPresenter.format_accuracy_badge(0.85, target=0.98)
    assert badge["is_passing"] is False
    assert badge["status_label"] == "DEGRADED"
    assert badge["display"] == "85.0%"
    assert badge["css_class"] == "badge--warning"


def test_academic_metric_presenter_latency():
    good_lat = AcademicMetricPresenter.format_latency_metric(4.25)
    assert good_lat["is_passing"] is True
    assert good_lat["status_label"] == "EXCELLENT"
    assert good_lat["display"] == "4.25 ms"

    high_lat = AcademicMetricPresenter.format_latency_metric(75.0)
    assert high_lat["is_passing"] is False
    assert high_lat["status_label"] == "HIGH"


def test_academic_metric_presenter_certificate_summary():
    cert = ModelAccuracyCertificate(
        service_pod_mapping_accuracy=1.0,
        pod_node_mapping_accuracy=0.99,
        owner_resolution_accuracy=1.0,
        concentration_detection_accuracy=0.98,
        is_statistically_sound=True,
    )
    summary = AcademicMetricPresenter.format_certificate_summary(cert)
    assert summary["is_statistically_sound"] is True
    assert summary["overall_status"] == "VERIFIED RESEARCH CERTIFICATE"
    assert summary["service_pod_mapping"]["is_passing"] is True
    assert summary["concentration_detection"]["is_passing"] is True


def test_dashboard_context_builder_structure():
    ctx = DashboardContextBuilder.build_dashboard_context()
    assert "title" in ctx
    assert "graph" in ctx
    assert "academic_certificate" in ctx
    assert "latency_info" in ctx
    assert "spofs" in ctx
    assert "categories" in ctx
    assert "node_risks" in ctx
    assert "scenarios" in ctx
    assert ctx["total_nodes"] >= 1
    assert ctx["total_services"] >= 1
    assert len(ctx["scenarios"]) == 8
