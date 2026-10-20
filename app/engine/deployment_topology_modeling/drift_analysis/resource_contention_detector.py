from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftType,
    DriftSeverity,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import DriftViolation
from app.engine.deployment_topology_modeling.drift_analysis.base_detector import BaseDriftDetector


class ResourceContentionDetector(BaseDriftDetector):
    """
    Reports containers declared without CPU/memory limits.

    Informational only: this does not measure node contention, and missing limits are not a
    placement rule, so its findings never count against constraint compliance or drift_detected.
    """

    @staticmethod
    def _extract_resources(declared_intent: Any | None) -> list[Any]:
        per_container_limits = getattr(declared_intent, "resource_limits_by_container", None)
        if per_container_limits:
            return [{"limits": limits} for limits in per_container_limits]
        if hasattr(declared_intent, "resource_limits"):
            rl = getattr(declared_intent, "resource_limits")
            if rl and getattr(rl, "cpu", None) and getattr(rl, "memory", None):
                return [{"limits": {"cpu": rl.cpu, "memory": rl.memory}}]
        if hasattr(declared_intent, "resources"):
            return getattr(declared_intent, "resources", [])
        if isinstance(declared_intent, dict):
            if "resource_limits" in declared_intent and isinstance(declared_intent["resource_limits"], dict):
                rl = declared_intent["resource_limits"]
                return [{"limits": {"cpu": rl.get("cpu"), "memory": rl.get("memory")}}]
            return declared_intent.get("resources", [])
        return []

    @staticmethod
    def _check_container_limits(
        service_name: str, idx: int, res_entry: Any
    ) -> DriftViolation | None:
        limits = res_entry.get("limits", {}) if isinstance(res_entry, dict) else {}
        missing = []
        if "cpu" not in limits:
            missing.append("cpu")
        if "memory" not in limits:
            missing.append("memory")

        if not missing:
            return None

        severity = DriftSeverity.LOW if len(missing) == 1 else DriftSeverity.MEDIUM
        return DriftViolation(
            drift_type=DriftType.RESOURCE_CONTENTION_DRIFT,
            severity=severity,
            description=(
                f"Container {idx} in service '{service_name}' is missing resource limits: "
                f"{', '.join(missing)}."
            ),
            expected="cpu and memory limits configured",
            actual=f"missing {', '.join(missing)}",
        )

    def detect(
        self,
        service_name: str,
        declared_intent: Any | None,
        runtime_pods: list[dict[str, Any]],
        cluster_nodes: list[dict[str, Any]],
    ) -> list[DriftViolation]:
        if not self.config.enforce_resource_limits or not declared_intent:
            return []

        resources_list = self._extract_resources(declared_intent)
        if not resources_list:
            return [
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
            ]

        violations: list[DriftViolation] = []
        for idx, res_entry in enumerate(resources_list):
            violation = self._check_container_limits(service_name, idx, res_entry)
            if violation:
                violations.append(violation)

        return violations
