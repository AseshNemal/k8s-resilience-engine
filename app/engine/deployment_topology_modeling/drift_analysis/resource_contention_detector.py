from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftType,
    DriftSeverity,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import DriftViolation
from app.engine.deployment_topology_modeling.drift_analysis.base_detector import BaseDriftDetector


class ResourceContentionDetector(BaseDriftDetector):
    """
    Detects resource contention risks, such as missing CPU/memory limits.
    
    Unbounded pods scheduled onto the same worker node create noisy-neighbor 
    vulnerabilities, risking unexpected OOMKills and involuntary pod evictions.
    """

    def detect(
        self,
        service_name: str,
        declared_intent: Any | None,
        runtime_pods: list[dict[str, Any]],
        cluster_nodes: list[dict[str, Any]],
    ) -> list[DriftViolation]:
        violations: list[DriftViolation] = []

        if not self.config.enforce_resource_limits or not declared_intent:
            return violations

        resources_list = (
            getattr(declared_intent, "resources", [])
            if hasattr(declared_intent, "resources")
            else declared_intent.get("resources", [])
            if isinstance(declared_intent, dict)
            else []
        )

        if not resources_list:
            violations.append(
                DriftViolation(
                    drift_type=DriftType.RESOURCE_CONTENTION_DRIFT,
                    severity=DriftSeverity.MEDIUM,
                    description=(
                        f"Service '{service_name}' has no resource specifications declared. "
                        f"Pods run without CPU/memory boundaries, risking node eviction."
                    ),
                    expected="cpu/memory limits configured",
                    actual="no resources declared",
                )
            )
            return violations

        for idx, res_entry in enumerate(resources_list):
            limits = res_entry.get("limits", {}) if isinstance(res_entry, dict) else {}
            has_cpu_limit = "cpu" in limits
            has_mem_limit = "memory" in limits

            if not has_cpu_limit or not has_mem_limit:
                missing = []
                if not has_cpu_limit:
                    missing.append("cpu")
                if not has_mem_limit:
                    missing.append("memory")
                violations.append(
                    DriftViolation(
                        drift_type=DriftType.RESOURCE_CONTENTION_DRIFT,
                        severity=DriftSeverity.LOW if len(missing) == 1 else DriftSeverity.MEDIUM,
                        description=(
                            f"Container {idx} in service '{service_name}' is missing resource limits: "
                            f"{', '.join(missing)}."
                        ),
                        expected="cpu and memory limits configured",
                        actual=f"missing {', '.join(missing)}",
                    )
                )

        return violations
