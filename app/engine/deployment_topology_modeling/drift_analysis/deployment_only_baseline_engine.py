from collections import Counter
from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    ServiceExposureResult,
    BaselineB2Result,
)


class DeploymentOnlyBaselineEngine:
    """
    Computes the Deployment-Only Baseline (B2).
    
    In the research proposal, Baseline B2 ranks services purely by deployment topology
    vulnerabilities (replica count, host concentration, and single-node exposure) 
    WITHOUT considering workflow call-graph dependencies.
    
    This provides Member 3 with the experimental control baseline to prove Hypothesis H2 & H3.
    """

    @staticmethod
    def generate_baseline_b2(
        service_exposures: list[ServiceExposureResult],
    ) -> BaselineB2Result:
        if not service_exposures:
            return BaselineB2Result()

        # Rank services by severity score descending, then concentration descending
        sorted_services = sorted(
            service_exposures,
            key=lambda s: (s.severity_score, s.replica_concentration, -s.ready_replicas),
            reverse=True,
        )

        service_rankings: list[dict[str, Any]] = []
        node_outage_impact: Counter[str] = Counter()
        total_spofs = 0

        for rank, exposure in enumerate(sorted_services, start=1):
            if exposure.exposed_failure_domains:
                total_spofs += len(exposure.exposed_failure_domains)
                for node in exposure.exposed_failure_domains:
                    node_outage_impact[node] += 1

            service_rankings.append(
                {
                    "rank": rank,
                    "service_name": exposure.service_name,
                    "exposure_level": exposure.exposure_level.value,
                    "severity_score": exposure.severity_score,
                    "replica_concentration": exposure.replica_concentration,
                    "ready_replicas": exposure.ready_replicas,
                    "exposed_nodes": exposure.exposed_failure_domains,
                }
            )

        most_vulnerable = sorted_services[0].service_name if sorted_services else None
        most_critical_node = node_outage_impact.most_common(1)[0][0] if node_outage_impact else None

        return BaselineB2Result(
            service_rankings=service_rankings,
            most_vulnerable_service=most_vulnerable,
            most_critical_worker_node=most_critical_node,
            total_single_points_of_failure=total_spofs,
        )
