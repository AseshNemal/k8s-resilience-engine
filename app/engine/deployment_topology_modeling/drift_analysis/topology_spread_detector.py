from collections import Counter
from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftType,
    DriftSeverity,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import DriftViolation
from app.engine.deployment_topology_modeling.drift_analysis.base_detector import BaseDriftDetector


class TopologySpreadDriftDetector(BaseDriftDetector):
    """
    Detects violations of declared Pod Topology Spread Constraints.
    
    Computes the actual skew across domains:
        skew = max(pods_per_domain) - min(pods_per_domain)
    and verifies whether it exceeds the declared maxSkew.
    """

    @staticmethod
    def _extract_constraints(declared_intent: Any | None) -> list[dict[str, Any]]:
        if not declared_intent:
            return []
        if hasattr(declared_intent, "topology_spread_constraints"):
            return getattr(declared_intent, "topology_spread_constraints", [])
        if isinstance(declared_intent, dict):
            return declared_intent.get("topology_spread_constraints", [])
        return []

    def _evaluate_constraint(
        self,
        service_name: str,
        constraint: dict[str, Any],
        runtime_pods: list[dict[str, Any]],
        node_labels: dict[str, dict[str, str]],
    ) -> DriftViolation | None:
        max_skew = constraint.get("max_skew", constraint.get("maxSkew", self.config.default_max_skew))
        topology_key = constraint.get("topology_key", constraint.get("topologyKey", self.config.failure_domain_key))
        when_unsatisfiable = constraint.get(
            "when_unsatisfiable",
            constraint.get("whenUnsatisfiable", "DoNotSchedule")
        )

        domain_counts: Counter[str] = Counter()
        for pod in runtime_pods:
            node_name = pod.get("node_name", pod.get("node"))
            if node_name:
                domain_val = node_labels.get(node_name, {}).get(topology_key, node_name)
                domain_counts[domain_val] += 1

        if not domain_counts:
            return None

        all_known_domains = {
            labels.get(topology_key, node_name)
            for node_name, labels in node_labels.items()
            if topology_key in labels or topology_key == self.config.failure_domain_key
        }
        if not all_known_domains:
            all_known_domains = set(domain_counts.keys())

        counts = [domain_counts.get(d, 0) for d in all_known_domains]
        actual_skew = max(counts) - min(counts)

        if actual_skew <= max_skew:
            return None

        severity = (
            DriftSeverity.HIGH
            if when_unsatisfiable == "DoNotSchedule"
            else DriftSeverity.MEDIUM
        )
        return DriftViolation(
            drift_type=DriftType.TOPOLOGY_SPREAD_DRIFT,
            severity=severity,
            description=(
                f"Service '{service_name}' violates topology spread constraint on '{topology_key}': "
                f"actual skew is {actual_skew} which exceeds declared maxSkew {max_skew} "
                f"(distribution: {dict(domain_counts)})."
            ),
            expected=max_skew,
            actual=actual_skew,
            affected_failure_domain=topology_key,
        )

    def detect(
        self,
        service_name: str,
        declared_intent: Any | None,
        runtime_pods: list[dict[str, Any]],
        cluster_nodes: list[dict[str, Any]],
    ) -> list[DriftViolation]:
        if not declared_intent or len(runtime_pods) <= 1:
            return []

        constraints = self._extract_constraints(declared_intent)
        if not constraints:
            return []

        node_labels: dict[str, dict[str, str]] = {
            node.get("name", ""): node.get("labels", {})
            for node in cluster_nodes
            if node.get("name")
        }

        violations: list[DriftViolation] = []
        for constraint in constraints:
            violation = self._evaluate_constraint(
                service_name, constraint, runtime_pods, node_labels
            )
            if violation:
                violations.append(violation)

        return violations
