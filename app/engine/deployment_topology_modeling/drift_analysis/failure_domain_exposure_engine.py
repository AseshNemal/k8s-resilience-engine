from typing import Any
from app.engine.deployment_topology_modeling import placement_metrics as pm
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    ExposureLevel,
    PLACEMENT_RULE_DRIFT_TYPES,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_config import DriftConfig
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    DriftViolation,
    ServiceExposureResult,
)


class FailureDomainExposureEngine:
    r"""Evaluates SA(s, {n}) for every worker node n (proposal section 7.4).

    SA(s, {n}) = 1 if at least one ready pod of s runs on a node other than n, else 0.
    A node n is an exposed failure domain of s exactly when SA(s, {n}) = 0.
    """

    def __init__(self, config: DriftConfig | None = None):
        self.config = config or DriftConfig()

    @staticmethod
    def _exposure_level(status: pm.ExposureStatus, ready_count: int) -> ExposureLevel:
        if status == pm.ExposureStatus.UNAVAILABLE:
            return ExposureLevel.UNAVAILABLE
        if status == pm.ExposureStatus.NOT_EXPOSED:
            return ExposureLevel.RESILIENT
        return ExposureLevel.SINGLE_REPLICA_EXPOSURE if ready_count == 1 else ExposureLevel.FULL_EXPOSURE

    def evaluate_service_exposure(
        self,
        service_name: str,
        runtime_pods: list[dict[str, Any]],
        violations: list[DriftViolation] | None = None,
        has_placement_rules: bool = False,
    ) -> ServiceExposureResult:
        violations = violations or []
        status = pm.exposure_status(runtime_pods)
        ready_count = pm.ready_replica_count(runtime_pods)
        ratio = pm.concentration_ratio(runtime_pods)

        # Compliance only means something for a service that declares a rule; otherwise it stays None.
        # A service can obey its rule and still be exposed (e.g. spread rule kept, but pods left Pending).
        rules_satisfied: bool | None = None
        if has_placement_rules:
            rules_satisfied = not any(v.drift_type in PLACEMENT_RULE_DRIFT_TYPES for v in violations)

        return ServiceExposureResult(
            service_name=service_name,
            total_replicas=len(runtime_pods),
            ready_replicas=ready_count,
            exposure_status=status,
            exposure_level=self._exposure_level(status, ready_count),
            concentrated=pm.is_concentrated(runtime_pods),
            replica_concentration=round(ratio, 4) if ratio is not None else None,
            exposed_failure_domains=pm.exposed_failure_domains(runtime_pods),
            ready_replicas_per_node=dict(sorted(pm.ready_replicas_per_node(runtime_pods).items())),
            has_placement_rules=has_placement_rules,
            placement_rules_satisfied=rules_satisfied,
            violations=violations,
        )
