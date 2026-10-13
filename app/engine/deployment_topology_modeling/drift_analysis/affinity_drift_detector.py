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

    def detect(
        self,
        service_name: str,
        declared_intent: Any | None,
        runtime_pods: list[dict[str, Any]],
        cluster_nodes: list[dict[str, Any]],
    ) -> list[DriftViolation]:
        violations: list[DriftViolation] = []

        if not declared_intent or len(runtime_pods) <= 1:
            return violations

        affinity_spec = (
            getattr(declared_intent, "affinity", {})
            if hasattr(declared_intent, "affinity")
            else declared_intent.get("affinity", {})
            if isinstance(declared_intent, dict)
            else {}
        )

        pod_anti_affinity = affinity_spec.get("podAntiAffinity", {}) if isinstance(affinity_spec, dict) else {}
        if not pod_anti_affinity:
            return violations

        # Map pods to nodes
        node_counts = Counter(
            pod.get("node_name", pod.get("node"))
            for pod in runtime_pods
            if pod.get("node_name", pod.get("node"))
        )

        # Check required (hard) anti-affinity
        required_rules = pod_anti_affinity.get("requiredDuringSchedulingIgnoredDuringExecution", [])
        if required_rules:
            for node_name, count in node_counts.items():
                if count > 1:
                    violations.append(
                        DriftViolation(
                            drift_type=DriftType.AFFINITY_DRIFT,
                            severity=DriftSeverity.CRITICAL,
                            description=(
                                f"Service '{service_name}' violates required podAntiAffinity: "
                                f"{count} pods co-located on worker node '{node_name}'."
                            ),
                            expected=1,
                            actual=count,
                            affected_failure_domain=node_name,
                        )
                    )

        # Check preferred (soft) anti-affinity
        preferred_rules = pod_anti_affinity.get("preferredDuringSchedulingIgnoredDuringExecution", [])
        if preferred_rules and not required_rules:
            for node_name, count in node_counts.items():
                if count > 1:
                    violations.append(
                        DriftViolation(
                            drift_type=DriftType.AFFINITY_DRIFT,
                            severity=DriftSeverity.MEDIUM,
                            description=(
                                f"Service '{service_name}' violates preferred podAntiAffinity: "
                                f"{count} pods co-located on worker node '{node_name}'."
                            ),
                            expected=1,
                            actual=count,
                            affected_failure_domain=node_name,
                        )
                    )

        return violations
