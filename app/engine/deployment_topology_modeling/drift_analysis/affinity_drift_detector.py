from collections import Counter
from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftType,
    DriftSeverity,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import DriftViolation
from app.engine.deployment_topology_modeling.drift_analysis.base_detector import BaseDriftDetector


class AffinityDriftDetector(BaseDriftDetector):
    """
    Detects violations of declared podAntiAffinity rules.
    
    Identifies cases where developers specified that replicas should not be scheduled
    together, but runtime conditions caused them to share the same worker node.
    """

    @staticmethod
    def _extract_pod_anti_affinity(declared_intent: Any | None) -> dict[str, Any]:
        if not declared_intent:
            return {}
        if hasattr(declared_intent, "affinity"):
            affinity_spec = getattr(declared_intent, "affinity", {})
        elif isinstance(declared_intent, dict):
            affinity_spec = declared_intent.get("affinity", {})
        else:
            affinity_spec = {}
        return affinity_spec.get("podAntiAffinity", {}) if isinstance(affinity_spec, dict) else {}

    @staticmethod
    def _check_co_location_violations(
        service_name: str,
        node_counts: Counter,
        severity: DriftSeverity,
        rule_type: str,
    ) -> list[DriftViolation]:
        violations: list[DriftViolation] = []
        for node_name, count in node_counts.items():
            if count > 1:
                violations.append(
                    DriftViolation(
                        drift_type=DriftType.AFFINITY_DRIFT,
                        severity=severity,
                        description=(
                            f"Service '{service_name}' violates {rule_type} podAntiAffinity: "
                            f"{count} pods co-located on worker node '{node_name}'."
                        ),
                        expected=1,
                        actual=count,
                        affected_failure_domain=node_name,
                    )
                )
        return violations

    def detect(
        self,
        service_name: str,
        declared_intent: Any | None,
        runtime_pods: list[dict[str, Any]],
        cluster_nodes: list[dict[str, Any]],
    ) -> list[DriftViolation]:
        if not declared_intent or len(runtime_pods) <= 1:
            return []

        pod_anti_affinity = self._extract_pod_anti_affinity(declared_intent)
        if not pod_anti_affinity:
            return []

        node_counts = Counter(
            pod.get("node_name", pod.get("node"))
            for pod in runtime_pods
            if pod.get("node_name", pod.get("node"))
        )

        required_rules = pod_anti_affinity.get("requiredDuringSchedulingIgnoredDuringExecution", [])
        if required_rules:
            return self._check_co_location_violations(service_name, node_counts, DriftSeverity.CRITICAL, "required")

        preferred_rules = pod_anti_affinity.get("preferredDuringSchedulingIgnoredDuringExecution", [])
        if preferred_rules:
            return self._check_co_location_violations(service_name, node_counts, DriftSeverity.MEDIUM, "preferred")

        return []

