from collections import Counter
from typing import Any
from app.engine.deployment_topology_modeling import placement_metrics as pm
from app.engine.deployment_topology_modeling.drift_analysis import intent_access as ia
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftType,
    DriftSeverity,
    FailureDomainType,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import DriftViolation
from app.engine.deployment_topology_modeling.drift_analysis.base_detector import BaseDriftDetector


class TopologySpreadDriftDetector(BaseDriftDetector):
    """Checks current placement against declared topologySpreadConstraints.

    Follows the Kubernetes definition of skew:
      eligible nodes  = nodes carrying the topologyKey label that also satisfy the pod's
                        nodeSelector / required nodeAffinity (default nodeAffinityPolicy: Honor)
      count(d)        = scheduled pods of the service on eligible nodes in domain d
      global minimum  = min over eligible domains, or 0 if fewer domains than minDomains
      skew            = max(count) - global minimum; violated when skew > maxSkew

    Kubernetes only enforces this when a pod is scheduled; evaluating it on the current state is
    what reveals drift after rescheduling, scaling or node loss.
    Assumption: the constraint's labelSelector selects exactly this service's pods.
    """

    def _domain_of(self, node_name: str, labels: dict[str, str], topology_key: str) -> str | None:
        if topology_key in labels:
            return labels[topology_key]
        if topology_key == FailureDomainType.HOSTNAME.value:
            return node_name
        return None

    def _pods_per_eligible_domain(
        self,
        declared_intent: Any,
        topology_key: str,
        runtime_pods: list[dict[str, Any]],
        nodes: dict[str, dict[str, str]],
    ) -> Counter[str]:
        """Scheduled pods per domain, over eligible nodes only; empty domains count as 0."""
        # Only nodes the pod is allowed on (its nodeSelector / required affinity) count, like in Kubernetes.
        node_domain: dict[str, str] = {}
        for node_name, labels in nodes.items():
            domain = self._domain_of(node_name, labels, topology_key)
            if domain is not None and ia.node_satisfies_required_placement(declared_intent, labels):
                node_domain[node_name] = domain

        # Start every domain at 0, so an empty node pulls the minimum down to 0.
        counts: Counter[str] = Counter(dict.fromkeys(set(node_domain.values()), 0))
        for pod in runtime_pods:
            node_name = pm.pod_node(pod)
            if node_name in node_domain:
                counts[node_domain[node_name]] += 1
        return counts

    @staticmethod
    def _skew(counts: Counter[str], min_domains: int | None) -> int:
        """max(count) - global minimum; the minimum is 0 when there are fewer domains than minDomains."""
        global_min = 0 if (min_domains and len(counts) < min_domains) else min(counts.values())
        return max(counts.values()) - global_min

    def _evaluate_constraint(
        self,
        service_name: str,
        declared_intent: Any,
        constraint: dict[str, Any],
        runtime_pods: list[dict[str, Any]],
        nodes: dict[str, dict[str, str]],
    ) -> DriftViolation | None:
        max_skew = constraint["max_skew"]
        if max_skew is None:
            return None
        topology_key = constraint["topology_key"] or self.config.failure_domain_key
        when_unsatisfiable = constraint["when_unsatisfiable"] or "DoNotSchedule"

        counts = self._pods_per_eligible_domain(declared_intent, topology_key, runtime_pods, nodes)
        if not counts or sum(counts.values()) == 0:
            return None

        # e.g. pods per node {2, 1, 0}: skew = 2 - 0 = 2, which breaks maxSkew 1.
        actual_skew = self._skew(counts, constraint["min_domains"])
        if actual_skew <= max_skew:
            return None

        # A hard rule (DoNotSchedule) that is broken is worse than a soft one (ScheduleAnyway).
        return DriftViolation(
            drift_type=DriftType.TOPOLOGY_SPREAD_DRIFT,
            severity=DriftSeverity.HIGH if when_unsatisfiable == "DoNotSchedule" else DriftSeverity.MEDIUM,
            description=(
                f"Service '{service_name}' violates topology spread constraint on '{topology_key}': "
                f"actual skew is {actual_skew} which exceeds declared maxSkew {max_skew} "
                f"(distribution: {dict(sorted(counts.items()))})."
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
        if not declared_intent:
            return []
        nodes = {n["name"]: ia.node_label_view(n) for n in cluster_nodes if n.get("name")}
        violations = []
        for constraint in ia.spread_constraints(declared_intent):
            violation = self._evaluate_constraint(service_name, declared_intent, constraint, runtime_pods, nodes)
            if violation:
                violations.append(violation)
        return violations
