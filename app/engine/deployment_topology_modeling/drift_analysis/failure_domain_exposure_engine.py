from collections import Counter
from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    ExposureLevel,
    DriftType,
    SEVERITY_WEIGHT_FULL_EXPOSURE,
    SEVERITY_WEIGHT_CONCENTRATION,
    SEVERITY_WEIGHT_SCALE_MISMATCH,
    SEVERITY_WEIGHT_RESOURCE_UNBOUNDED,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_config import DriftConfig
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    DriftViolation,
    ServiceExposureResult,
)


class FailureDomainExposureEngine:
    """
    Formal Mathematical Failure-Domain Exposure Evaluation Engine.
    
    Evaluates:
        SA(s, {n}) = |P_s \ P_{s,n}|
    across all worker nodes n in cluster N to identify single failure-domain vulnerabilities.
    """

    def __init__(self, config: DriftConfig | None = None):
        self.config = config or DriftConfig()

    def evaluate_service_exposure(
        self,
        service_name: str,
        runtime_pods: list[dict[str, Any]],
        cluster_nodes: list[dict[str, Any]],
        violations: list[DriftViolation] | None = None,
    ) -> ServiceExposureResult:
        violations = violations or []
        total_replicas = len(runtime_pods)
        ready_pods = [
            p for p in runtime_pods
            if p.get("is_ready", p.get("ready", False))
        ]
        ready_count = len(ready_pods)

        if ready_count == 0:
            return ServiceExposureResult(
                service_name=service_name,
                total_replicas=total_replicas,
                ready_replicas=0,
                replica_concentration=1.0 if total_replicas > 0 else 0.0,
                exposure_level=ExposureLevel.FULL_EXPOSURE,
                exposed_failure_domains=[],
                partial_loss_domains={},
                violations=violations,
                severity_score=1.0,
            )

        # Map ready pods to worker nodes
        pods_per_node = Counter(
            p.get("node_name", p.get("node"))
            for p in ready_pods
            if p.get("node_name", p.get("node"))
        )

        max_node_count = max(pods_per_node.values()) if pods_per_node else 0
        replica_concentration = max_node_count / ready_count if ready_count > 0 else 0.0

        # Simulate failure for each node hosting pods
        exposed_nodes: list[str] = []
        partial_loss_map: dict[str, int] = {}

        for node_name, count_on_node in pods_per_node.items():
            partial_loss_map[node_name] = count_on_node
            surviving_count = ready_count - count_on_node
            # If 0 replicas survive node failure
            if surviving_count == 0:
                exposed_nodes.append(node_name)

        # Classify Exposure Level
        if ready_count == 1:
            exposure_level = ExposureLevel.SINGLE_REPLICA_EXPOSURE
        elif len(exposed_nodes) > 0:
            exposure_level = ExposureLevel.FULL_EXPOSURE
        elif replica_concentration >= self.config.concentration_threshold:
            exposure_level = ExposureLevel.PARTIAL_EXPOSURE
        else:
            exposure_level = ExposureLevel.RESILIENT

        # Calculate mathematical severity score (0.0 to 1.0)
        has_full_exposure = 1.0 if (len(exposed_nodes) > 0 or (ready_count == 1 and self.config.treat_single_replica_as_exposed)) else 0.0
        has_scale_mismatch = 1.0 if any(v.drift_type == DriftType.SCALE_DRIFT for v in violations) else 0.0
        has_unbounded_resource = 1.0 if any(v.drift_type == DriftType.RESOURCE_CONTENTION_DRIFT for v in violations) else 0.0

        severity_score = (
            SEVERITY_WEIGHT_FULL_EXPOSURE * has_full_exposure
            + SEVERITY_WEIGHT_CONCENTRATION * replica_concentration
            + SEVERITY_WEIGHT_SCALE_MISMATCH * has_scale_mismatch
            + SEVERITY_WEIGHT_RESOURCE_UNBOUNDED * has_unbounded_resource
        )
        severity_score = min(max(severity_score, 0.0), 1.0)

        return ServiceExposureResult(
            service_name=service_name,
            total_replicas=total_replicas,
            ready_replicas=ready_count,
            replica_concentration=round(replica_concentration, 4),
            exposure_level=exposure_level,
            exposed_failure_domains=exposed_nodes,
            partial_loss_domains=partial_loss_map,
            violations=violations,
            severity_score=round(severity_score, 4),
        )
