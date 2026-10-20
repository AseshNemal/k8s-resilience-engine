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


def _section(obj: Dict[str, Any], key: str) -> Dict[str, Any]:
    """obj[key] as a dict; YAML may give None for an empty section."""
    return obj.get(key) or {}


def _container_resources(containers: List[Dict[str, Any]], kind: str) -> List[Dict[str, str]]:
    return [_str_dict(_section(c, "resources").get(kind)) for c in containers]


def _first_container_limits(limits_by_container: List[Dict[str, str]]) -> ResourceLimits:
    if not limits_by_container:
        return ResourceLimits()
    return ResourceLimits(cpu=limits_by_container[0].get("cpu"), memory=limits_by_container[0].get("memory"))


def _parse_deployment(item: Dict[str, Any]) -> DeclaredIntent:
    metadata = _section(item, "metadata")
    spec = _section(item, "spec")
    template = _section(spec, "template")
    template_spec = _section(template, "spec")
    containers = template_spec.get("containers") or []
    affinity = _section(template_spec, "affinity")
    selector_spec = _section(spec, "selector")
    warnings: List[str] = []
    limits_by_container = _container_resources(containers, "limits")

    return DeclaredIntent(
        service_name=metadata.get("name", "unknown"),
        namespace=metadata.get("namespace") or "default",
        expected_replicas=spec.get("replicas", 1),
        resource_limits=_first_container_limits(limits_by_container),
        spread_constraints=[
            _parse_spread_constraint(c, i, warnings)
            for i, c in enumerate(template_spec.get("topologySpreadConstraints") or [])
        ],
        has_node_affinity="nodeAffinity" in affinity,
        affinity=affinity,
        selector=_str_dict(selector_spec.get("matchLabels")),
        template_labels=_str_dict(_section(template, "metadata").get("labels")),
        # A Service is only as ready as each pod; every container needs a readiness probe.
        readiness_probe_configured=bool(containers) and all(c.get("readinessProbe") for c in containers),
        pod_anti_affinity_configured="podAntiAffinity" in affinity,
        pod_affinity_configured="podAffinity" in affinity,
        node_selector=_str_dict(template_spec.get("nodeSelector")),
        tolerations=template_spec.get("tolerations") or [],
        selector_match_expressions=selector_spec.get("matchExpressions") or [],
        node_affinity_rules=_section(affinity, "nodeAffinity"),
        pod_affinity_rules=_section(affinity, "podAffinity"),
        pod_anti_affinity_rules=_section(affinity, "podAntiAffinity"),
        resource_requests=_container_resources(containers, "requests"),
        resource_limits_by_container=limits_by_container,
        parse_warnings=warnings,
        is_topology_compliant=None,
    )


def parse_declared_intents(manifests: List[Dict[str, Any]]) -> List[DeclaredIntent]:
    """Extracts declared placement intent from every Deployment document."""
    return [_parse_deployment(item) for item in manifests if item.get("kind", "") == "Deployment"]


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
