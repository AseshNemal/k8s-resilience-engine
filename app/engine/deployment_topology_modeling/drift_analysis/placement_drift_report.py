import json
from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    ClusterExposureReport,
    PlacementDriftDiffReport,
    BaselineB2Result,
)


def _pct(value: float | None) -> str:
    return "not measurable (empty denominator)" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


class PlacementDriftReport:
    """Markdown / JSON export of exposure and drift results."""

    @staticmethod
    def generate_markdown_summary(
        exposure_report: ClusterExposureReport,
        baseline_b2: BaselineB2Result | None = None,
        diff_report: PlacementDriftDiffReport | None = None,
    ) -> str:
        r = exposure_report
        lines = [
            "# Kubernetes Deployment Topology Exposure & Drift Report",
            f"**Timestamp:** {r.timestamp}",
            f"**Total Services:** {r.total_services} "
            f"(evaluated: {r.evaluated_services}, unavailable before failure: {len(r.unavailable_services)})",
            f"**Exposed Services:** {r.exposed_service_count} — exposure rate {_pct(r.exposure_rate)}",
            f"**Constraint Compliance Rate:** {_pct(r.constraint_compliance_rate)} "
            f"over {r.services_with_placement_rules} services with placement rules",
            f"**Mean Replica Concentration:** {_num(r.mean_concentration)}",
            f"**Analysis Runtime Overhead:** {r.runtime_overhead_ms:.2f} ms",
            "",
            "## Service Exposure Rankings (Baseline B2)",
        ]

        if baseline_b2 and baseline_b2.service_rankings:
            lines.extend([
                f"- **Most Vulnerable Service:** {baseline_b2.most_vulnerable_service}",
                f"- **Node Exposing Most Services:** {baseline_b2.most_critical_worker_node}",
                f"- **Total Single Points of Failure:** {baseline_b2.total_single_points_of_failure}",
                "",
                "| Rank | Service | Status | Concentration | Ready Replicas | Exposed Nodes |",
                "|---|---|---|---|---|---|",
            ])
            for row in baseline_b2.service_rankings:
                nodes_str = ", ".join(row.get("exposed_nodes", [])) or "None"
                lines.append(
                    f"| {row.get('rank')} | {row.get('service_name')} | "
                    f"{row.get('exposure_status', row.get('exposure_level'))} | "
                    f"{_num(row.get('replica_concentration'))} | {row.get('ready_replicas')} | {nodes_str} |"
                )
        else:
            lines.append("No service rankings available.")

        if diff_report:
            lines.extend([
                "",
                "## Placement Drift Analysis (Snapshot Diff)",
                f"- **Compared:** {diff_report.baseline_snapshot_id} -> {diff_report.target_snapshot_id}",
                f"- **Placement Drift Rate (D_rate):** {_pct(diff_report.placement_drift_rate)} "
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
