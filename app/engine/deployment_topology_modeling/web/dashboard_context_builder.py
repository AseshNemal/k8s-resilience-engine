"""
Dashboard Context Builder: Assembles complete server-side rendering context
for Member 2's Multi-Tab Executive Dashboard and Academic Validation UI.
Component: Component 2 - Deployment Topology Modeling
"""
from typing import Any, Dict, List, Optional
from app.engine.deployment_topology_modeling.service import (
    get_sample_deployment,
    get_manifest_intent,
    analyze_resilience_drift,
    get_academic_accuracy_certificate,
    get_drift_spofs,
    get_drift_categories_summary,
    get_concentration_rankings,
    get_node_risk_rankings,
)
from app.engine.deployment_topology_modeling.web.academic_metric_presenter import (
    AcademicMetricPresenter,
)
from experiments.placement_drift.disruption_orchestrator import DisruptionOrchestrator


class DashboardContextBuilder:
    """Orchestrates multi-phase domain outputs into a structured UI rendering context."""

    @classmethod
    def build_dashboard_context(cls, namespace: str = "default") -> Dict[str, Any]:
        """Assembles unified SSR context encompassing all 5 research phases."""
        graph = get_sample_deployment()
        manifest_intents = get_manifest_intent()
        drift_report, b2_baseline = analyze_resilience_drift(namespace=namespace)
        certificate = get_academic_accuracy_certificate()

        cert_summary = AcademicMetricPresenter.format_certificate_summary(certificate)
        latency_info = AcademicMetricPresenter.format_latency_metric(
            getattr(graph.validation, "runtime_overhead_ms", 12.5)
        )

        spofs = get_drift_spofs(namespace=namespace)
        categories = get_drift_categories_summary(namespace=namespace)
        concentration_ranks = get_concentration_rankings(namespace=namespace)
        node_risks = get_node_risk_rankings(namespace=namespace)

        scenarios_meta = [
            {
                "id": cls_scenario().scenario_id,
                "name": cls_scenario().scenario_name,
                "type": cls_scenario().disruption_type.value,
                "target": cls_scenario().target_service,
            }
            for cls_scenario in DisruptionOrchestrator.ALL_SCENARIO_CLASSES
        ]

        return {
            "title": "Deployment Topology Modeling & Resilience Drift",
            "active_tab": "topology",
            "namespace": namespace,
            "graph": graph,
            "manifest_intents": manifest_intents,
            "drift_report": drift_report,
            "b2_baseline": b2_baseline,
            "academic_certificate": cert_summary,
            "latency_info": latency_info,
            "spofs": spofs,
            "spof_count": len(spofs),
            "categories": categories,
            "concentration_ranks": concentration_ranks,
            "node_risks": node_risks,
            "scenarios": scenarios_meta,
            "total_nodes": len(graph.nodes),
            "total_services": len(graph.services),
            "total_pods": sum(len(s.pods) for s in graph.services),
            "highest_concentration": max((s.replica_concentration or 0.0 for s in graph.services), default=0.0),
        }
