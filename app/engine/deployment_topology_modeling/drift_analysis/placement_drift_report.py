import json
from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    ClusterExposureReport,
    PlacementDriftDiffReport,
    BaselineB2Result,
)


class PlacementDriftReport:
    """
    Formulation and export formatter for drift and exposure analysis results.
    
    Generates structured summaries for academic papers, API contracts, and UI dashboards.
    """

    @staticmethod
    def generate_markdown_summary(
        exposure_report: ClusterExposureReport,
        baseline_b2: BaselineB2Result | None = None,
        diff_report: PlacementDriftDiffReport | None = None,
    ) -> str:
        lines = [
            "# Kubernetes Deployment Topology Resilience & Drift Report",
            f"**Timestamp:** {exposure_report.timestamp}",
            f"**Total Services:** {exposure_report.total_services}",
            f"**Exposed Services (Single Point of Failure):** {exposure_report.exposed_service_count} "
            f"({exposure_report.exposure_rate * 100:.1f}%)",
            f"**Constraint Compliance Rate:** {exposure_report.constraint_compliance_rate * 100:.1f}%",
            f"**Mean Replica Concentration:** {exposure_report.mean_concentration:.2f}",
            f"**Analysis Runtime Overhead:** {exposure_report.runtime_overhead_ms:.2f} ms",
            "",
            "## Service Exposure Rankings (Baseline B2)",
        ]

        if baseline_b2 and baseline_b2.service_rankings:
            lines.extend([
                f"- **Most Vulnerable Service:** {baseline_b2.most_vulnerable_service}",
                f"- **Most Critical Worker Node:** {baseline_b2.most_critical_worker_node}",
                f"- **Total Single Points of Failure:** {baseline_b2.total_single_points_of_failure}",
                "",
                "| Rank | Service | Level | Severity Score | Concentration | Ready Replicas | Exposed Nodes |",
                "|---|---|---|---|---|---|---|",
            ])
            for r in baseline_b2.service_rankings:
                nodes_str = ", ".join(r.get("exposed_nodes", [])) or "None"
                lines.append(
                    f"| {r.get('rank')} | {r.get('service_name')} | {r.get('exposure_level')} | "
                    f"{r.get('severity_score'):.2f} | {r.get('replica_concentration'):.2f} | "
                    f"{r.get('ready_replicas')} | {nodes_str} |"
                )
        else:
            lines.append("No service rankings available.")

        if diff_report:
            lines.extend([
                "",
                "## Placement Drift Analysis (Snapshot Diff)",
                f"- **Compared:** {diff_report.baseline_snapshot_id} -> {diff_report.target_snapshot_id}",
                f"- **Placement Drift Rate (D_rate):** {diff_report.placement_drift_rate * 100:.1f}% "
                f"({diff_report.drifted_services_count}/{diff_report.total_services_compared} services)",
                f"- **Newly Exposed Services:** {', '.join(diff_report.newly_exposed_services) or 'None'}",
                f"- **Remediated Services:** {', '.join(diff_report.remediated_services) or 'None'}",
            ])

        return "\n".join(lines)

    @staticmethod
    def to_dict(report: ClusterExposureReport) -> dict[str, Any]:
        return report.model_dump()

    @staticmethod
    def to_json(report: ClusterExposureReport, indent: int = 2) -> str:
        return json.dumps(report.model_dump(), indent=indent)
