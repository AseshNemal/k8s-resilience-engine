"""
Dashboard Context Builder: server-side rendering context for Member 2's dashboard.
Component: Component 2 - Deployment Topology Modeling

All panels come from ONE analysis, so topology, drift and Baseline B2 always describe the same
data. When no cluster is reachable the sample fixture is used and `data_source` says "sample".
"""
from typing import Any, Dict
from app.engine.deployment_topology_modeling.service import (
    ClusterUnavailableError,
    analyze_live_topology,
    analyze_sample_topology,
    categories_from_report,
    get_academic_accuracy_certificate,
    get_manifest_intent,
    node_risks_from_analysis,
    spofs_from_report,
)
from app.engine.deployment_topology_modeling.web.academic_metric_presenter import (
    AcademicMetricPresenter,
)
from experiments.placement_drift.disruption_orchestrator import DisruptionOrchestrator


class DashboardContextBuilder:

    @classmethod
    def build_dashboard_context(cls, namespace: str = "default") -> Dict[str, Any]:
        try:
            analysis = analyze_live_topology(namespace=namespace)
        except ClusterUnavailableError:
            analysis = analyze_sample_topology()

        graph, report, b2 = analysis.graph, analysis.report, analysis.baseline_b2
        certificate = get_academic_accuracy_certificate()
        spofs = spofs_from_report(report)
        scenarios = [cls_scenario() for cls_scenario in DisruptionOrchestrator.ALL_SCENARIO_CLASSES]

        return {
            "title": "Deployment Topology Modeling & Resilience Drift",
            "active_tab": "topology",
            "namespace": namespace,
            "data_source": analysis.data_source,
            "graph": graph,
            "manifest_intents": get_manifest_intent(),
            "drift_report": report,
            "b2_baseline": b2,
            "academic_certificate": AcademicMetricPresenter.format_certificate_summary(certificate),
            "latency_info": AcademicMetricPresenter.format_latency_metric(graph.validation.runtime_overhead_ms),
            "spofs": spofs,
            "spof_count": len(spofs),
            "categories": categories_from_report(report),
            "node_risks": node_risks_from_analysis(analysis),
            "scenarios": [
                {"id": s.scenario_id, "name": s.scenario_name, "type": s.disruption_type.value,
                 "target": s.target_service, "execution_mode": s.execution_mode.value}
                for s in scenarios
            ],
            "total_nodes": len(graph.nodes),
            "total_services": len(graph.services),
            "total_pods": sum(len(s.pods) for s in graph.services),
            "highest_concentration": max(
                (s.replica_concentration for s in graph.services if s.replica_concentration is not None),
                default=None,
            ),
        }
