from typing import List, Dict, Any
from app.engine.deployment_topology_modeling.manifest_parsing.manifest_models import (
    DeclaredIntent,
    DeclaredService,
    ResourceLimits,
    TopologyConstraint,
)

REQUIRED_SPREAD_FIELDS = ("maxSkew", "topologyKey", "whenUnsatisfiable")


def _str_dict(value: Any) -> Dict[str, str]:
    return {str(k): str(v) for k, v in (value or {}).items()}


def _parse_spread_constraint(raw: Dict[str, Any], index: int, warnings: List[str]) -> TopologyConstraint:
    missing = [f for f in REQUIRED_SPREAD_FIELDS if raw.get(f) is None]
    if missing:
        warnings.append(
            f"topologySpreadConstraints[{index}] is missing required field(s) {missing}; "
            "Kubernetes would reject this manifest."
        )
    return TopologyConstraint(
        max_skew=raw.get("maxSkew"),
        topology_key=raw.get("topologyKey"),
        when_unsatisfiable=raw.get("whenUnsatisfiable"),
        min_domains=raw.get("minDomains"),
        label_selector=raw.get("labelSelector"),
    )


def parse_declared_intents(manifests: List[Dict[str, Any]]) -> List[DeclaredIntent]:
    """Extracts declared placement intent from every Deployment document."""
    intents = []
    for item in manifests:
        if item.get("kind", "") != "Deployment":
            continue

        metadata = item.get("metadata", {}) or {}
        spec = item.get("spec", {}) or {}
        template = spec.get("template", {}) or {}
        template_spec = template.get("spec", {}) or {}
        containers = template_spec.get("containers", []) or []
        warnings: List[str] = []

        requests_by_container = []
        limits_by_container = []
        for container in containers:
            resources = container.get("resources", {}) or {}
            requests_by_container.append(_str_dict(resources.get("requests")))
            limits_by_container.append(_str_dict(resources.get("limits")))

        res_limits = ResourceLimits()
        if limits_by_container:
            res_limits.cpu = limits_by_container[0].get("cpu")
            res_limits.memory = limits_by_container[0].get("memory")

        spread_constraints = [
            _parse_spread_constraint(c, i, warnings)
            for i, c in enumerate(template_spec.get("topologySpreadConstraints", []) or [])
        ]

        affinity = template_spec.get("affinity", {}) or {}
        selector_spec = spec.get("selector", {}) or {}

        intents.append(DeclaredIntent(
            service_name=metadata.get("name", "unknown"),
            namespace=metadata.get("namespace") or "default",
            expected_replicas=spec.get("replicas", 1),
            resource_limits=res_limits,
            spread_constraints=spread_constraints,
            has_node_affinity="nodeAffinity" in affinity,
            affinity=affinity,
            selector=_str_dict(selector_spec.get("matchLabels")),
            template_labels=_str_dict((template.get("metadata", {}) or {}).get("labels")),
            # A Service is only as ready as each pod; every container needs a readiness probe.
            readiness_probe_configured=bool(containers) and all(c.get("readinessProbe") for c in containers),
            pod_anti_affinity_configured="podAntiAffinity" in affinity,
            pod_affinity_configured="podAffinity" in affinity,
            node_selector=_str_dict(template_spec.get("nodeSelector")),
            tolerations=template_spec.get("tolerations", []) or [],
            selector_match_expressions=selector_spec.get("matchExpressions", []) or [],
            node_affinity_rules=affinity.get("nodeAffinity", {}) or {},
            pod_affinity_rules=affinity.get("podAffinity", {}) or {},
            pod_anti_affinity_rules=affinity.get("podAntiAffinity", {}) or {},
            resource_requests=requests_by_container,
            resource_limits_by_container=limits_by_container,
            parse_warnings=warnings,
            is_topology_compliant=None,
        ))
    return intents


def parse_declared_services(manifests: List[Dict[str, Any]]) -> List[DeclaredService]:
    """Extracts declared Service selectors (TAF p.7: Deployments, Services, ReplicaSets)."""
    services = []
    for item in manifests:
        if item.get("kind", "") != "Service":
            continue
        metadata = item.get("metadata", {}) or {}
        services.append(DeclaredService(
            name=metadata.get("name", "unknown"),
            namespace=metadata.get("namespace") or "default",
            selector=_str_dict((item.get("spec", {}) or {}).get("selector")),
        ))
    return services
