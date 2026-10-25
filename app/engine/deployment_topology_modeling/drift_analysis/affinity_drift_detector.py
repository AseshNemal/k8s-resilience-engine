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


class AffinityDriftDetector(BaseDriftDetector):
    """Checks runtime placement against declared node placement rules and pod anti-affinity.

    Node rules: nodeSelector and required nodeAffinity are hard (CRITICAL when violated);
    preferred nodeAffinity is soft (MEDIUM when no preference matches).

    Pod anti-affinity: each term is evaluated on its own topologyKey. A term is violated when
    more than one pod of the service shares a domain of that key.
    Assumption: the term's labelSelector selects this service's own pods (self anti-affinity,
    the usual replica-spreading pattern); terms targeting other services are not evaluated.
    """

    def _domain_of(self, node_name: str, labels: dict[str, str] | None, topology_key: str) -> str | None:
        if labels and topology_key in labels:
            return labels[topology_key]
        if topology_key == FailureDomainType.HOSTNAME.value:
            return node_name
        return None

    def _node_rule_violations(
        self,
        service_name: str,
        declared_intent: Any,
        runtime_pods: list[dict[str, Any]],
        nodes: dict[str, dict[str, str]],
    ) -> list[DriftViolation]:
        # Hard rules ("must run on these nodes") vs soft rules ("prefer these nodes").
        has_hard = bool(ia.node_selector(declared_intent) or ia.required_node_terms(declared_intent))
        preferred = ia.preferred_node_terms(declared_intent)
        violations: list[DriftViolation] = []
        # Check each running pod against the labels of the node it actually landed on.
        for pod in runtime_pods:
            node_name = pm.pod_node(pod)
            labels = nodes.get(node_name) if node_name else None
            if labels is None:
                continue
            if has_hard and not ia.node_satisfies_required_placement(declared_intent, labels):
                violations.append(DriftViolation(
                    drift_type=DriftType.AFFINITY_DRIFT,
                    severity=DriftSeverity.CRITICAL,
                    description=(f"Pod '{pod.get('name', 'unknown')}' for service '{service_name}' is on "
                                 f"node '{node_name}', which violates required node placement constraints."),
                    expected="nodeSelector / required nodeAffinity",
                    actual=node_name,
                    affected_failure_domain=node_name,
                ))
            elif preferred and not any(ia.matches_node_term(labels, term) for term in preferred):
                violations.append(DriftViolation(
                    drift_type=DriftType.AFFINITY_DRIFT,
                    severity=DriftSeverity.MEDIUM,
                    description=(f"Pod '{pod.get('name', 'unknown')}' for service '{service_name}' "
                                 f"does not meet any preferred node-affinity rule on '{node_name}'."),
                    expected="preferred nodeAffinity",
                    actual=node_name,
                    affected_failure_domain=node_name,
                ))
        return violations

    def _anti_affinity_violations(
        self,
        service_name: str,
        declared_intent: Any,
        runtime_pods: list[dict[str, Any]],
        nodes: dict[str, dict[str, str]],
    ) -> list[DriftViolation]:
        violations: list[DriftViolation] = []
        # Anti-affinity says "don't put two of my pods in the same domain" (usually the same node),
        # so any domain holding more than one pod breaks it.
        for term, is_required in ia.pod_anti_affinity_terms(declared_intent):
            topology_key = term.get("topologyKey") or self.config.failure_domain_key
            per_domain = self._pods_per_domain(runtime_pods, nodes, topology_key)
            violations += [
                self._anti_affinity_violation(service_name, topology_key, domain, count, is_required)
                for domain, count in sorted(per_domain.items())
                if count > 1
            ]
        return violations

    def _pods_per_domain(
        self, runtime_pods: list[dict[str, Any]], nodes: dict[str, dict[str, str]], topology_key: str
    ) -> Counter[str]:
        per_domain: Counter[str] = Counter()
        for pod in runtime_pods:
            node_name = pm.pod_node(pod)
            domain = self._domain_of(node_name, nodes.get(node_name), topology_key) if node_name else None
            if domain is not None:
                per_domain[domain] += 1
        return per_domain

    @staticmethod
    def _anti_affinity_violation(
        service_name: str, topology_key: str, domain: str, count: int, is_required: bool
    ) -> DriftViolation:
        rule_type = "required" if is_required else "preferred"
        return DriftViolation(
            drift_type=DriftType.AFFINITY_DRIFT,
            severity=DriftSeverity.CRITICAL if is_required else DriftSeverity.MEDIUM,
            description=(
                f"Service '{service_name}' violates {rule_type} podAntiAffinity on "
                f"'{topology_key}': {count} pods share domain '{domain}'."
            ),
            expected=1,
            actual=count,
            affected_failure_domain=domain,
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
        return (
            self._node_rule_violations(service_name, declared_intent, runtime_pods, nodes)
            + self._anti_affinity_violations(service_name, declared_intent, runtime_pods, nodes)
        )
