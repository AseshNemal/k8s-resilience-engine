from typing import Any
from app.engine.deployment_topology_modeling.placement_metrics import ExposureStatus
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    ServiceExposureResult,
    BaselineB2Result,
)

_STATUS_ORDER = {
    ExposureStatus.EXPOSED: 0,
    ExposureStatus.NOT_EXPOSED: 1,
    ExposureStatus.UNAVAILABLE: 2,
}


class DeploymentOnlyBaselineEngine:
    """Deployment-Only Baseline (B2): ranks services by exposure using placement data only
    (Study Guide section 18), with no workflow context.

    The ranking is lexicographic so that every position can be explained without weights:
      1. exposure status: EXPOSED, then NOT_EXPOSED, then UNAVAILABLE
      2. higher concentration ratio first
      3. fewer ready replicas first
      4. service name (deterministic tie-break)
    """

    @staticmethod
    def _sort_key(e: ServiceExposureResult) -> tuple:
        ratio = e.replica_concentration if e.replica_concentration is not None else 0.0
        return (_STATUS_ORDER[e.exposure_status], -ratio, e.ready_replicas, e.service_name)

    @classmethod
    def generate_baseline_b2(cls, service_exposures: list[ServiceExposureResult]) -> BaselineB2Result:
        if not service_exposures:
            return BaselineB2Result()

        ranked = sorted(service_exposures, key=cls._sort_key)

        # Which services each node would take down on its own.
        exposed_by_node: dict[str, list[str]] = {}
        for e in ranked:
            for node in e.exposed_failure_domains:
                exposed_by_node.setdefault(node, []).append(e.service_name)

        rankings: list[dict[str, Any]] = [
            {
                "rank": rank,
                "service_name": e.service_name,
                "exposure_status": e.exposure_status.value,
                "exposure_level": e.exposure_level.value,
                "concentrated": e.concentrated,
                "replica_concentration": e.replica_concentration,
                "ready_replicas": e.ready_replicas,
                "exposed_nodes": e.exposed_failure_domains,
            }
            for rank, e in enumerate(ranked, start=1)
        ]

        # The node that would take down the most services; ties go to the first name, so the answer never changes.
        most_critical_node = None
        if exposed_by_node:
            most_critical_node = min(exposed_by_node, key=lambda n: (-len(exposed_by_node[n]), n))

        return BaselineB2Result(
            service_rankings=rankings,
            most_vulnerable_service=ranked[0].service_name,
            most_critical_worker_node=most_critical_node,
            exposed_services_by_node=dict(sorted(exposed_by_node.items())),
            total_single_points_of_failure=sum(len(v) for v in exposed_by_node.values()),
        )
