"""Uniform read access to declared intent and Kubernetes node-label matching.

Declared intent reaches the detectors either as a manifest_models.DeclaredIntent or as a plain
dict, with snake_case or Kubernetes camelCase keys. Every detector reads intent through these
helpers so a field cannot be silently missed because of its spelling.
"""
from typing import Any


def get_field(intent: Any, *names: str, default: Any = None) -> Any:
    # Try each spelling (e.g. node_selector, nodeSelector) on a dict or an object; the first one set wins.
    for name in names:
        if isinstance(intent, dict):
            if intent.get(name) is not None:
                return intent[name]
        elif getattr(intent, name, None) is not None:
            return getattr(intent, name)
    return default


def affinity_spec(intent: Any) -> dict[str, Any]:
    affinity = dict(get_field(intent, "affinity", default={}) or {})
    for key, *names in (
        ("nodeAffinity", "node_affinity_rules"),
        ("podAffinity", "pod_affinity_rules"),
        ("podAntiAffinity", "pod_anti_affinity_rules"),
    ):
        if not affinity.get(key):
            value = get_field(intent, *names, default={})
            if value:
                affinity[key] = value
    return affinity


def node_selector(intent: Any) -> dict[str, str]:
    return dict(get_field(intent, "node_selector", "nodeSelector", default={}) or {})


def _normalise_constraint(raw: Any) -> dict[str, Any]:
    c = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
    return {
        "max_skew": c.get("max_skew", c.get("maxSkew")),
        "topology_key": c.get("topology_key", c.get("topologyKey")),
        "when_unsatisfiable": c.get("when_unsatisfiable", c.get("whenUnsatisfiable")),
        "min_domains": c.get("min_domains", c.get("minDomains")),
        "label_selector": c.get("label_selector", c.get("labelSelector")),
    }


def spread_constraints(intent: Any) -> list[dict[str, Any]]:
    raw = get_field(
        intent, "spread_constraints", "topology_spread_constraints", "topologySpreadConstraints",
        default=[],
    )
    return [_normalise_constraint(c) for c in raw or []]


def required_node_terms(intent: Any) -> list[dict[str, Any]]:
    # "required..." = a hard rule: the scheduler will not place the pod on a node that breaks it.
    node_affinity = affinity_spec(intent).get("nodeAffinity", {}) or {}
    required = node_affinity.get("requiredDuringSchedulingIgnoredDuringExecution", {}) or {}
    return required.get("nodeSelectorTerms", []) or []


def preferred_node_terms(intent: Any) -> list[dict[str, Any]]:
    # "preferred..." = a soft rule: the scheduler tries, but may place the pod elsewhere.
    node_affinity = affinity_spec(intent).get("nodeAffinity", {}) or {}
    preferred = node_affinity.get("preferredDuringSchedulingIgnoredDuringExecution", []) or []
    return [item.get("preference", {}) or {} for item in preferred]


def pod_anti_affinity_terms(intent: Any) -> list[tuple[dict[str, Any], bool]]:
    """Returns (PodAffinityTerm, is_required) pairs."""
    anti = affinity_spec(intent).get("podAntiAffinity", {}) or {}
    terms = [(t, True) for t in anti.get("requiredDuringSchedulingIgnoredDuringExecution", []) or []]
    terms += [
        (item.get("podAffinityTerm", {}) or {}, False)
        for item in anti.get("preferredDuringSchedulingIgnoredDuringExecution", []) or []
    ]
    return terms


def has_placement_rules(intent: Any) -> bool:
    """True when the intent declares a rule that the affinity or spread detector evaluates."""
    if not intent:
        return False
    return bool(
        node_selector(intent)
        or required_node_terms(intent)
        or preferred_node_terms(intent)
        or pod_anti_affinity_terms(intent)
        or spread_constraints(intent)
    )


def matches_expression(labels: dict[str, str], expression: dict[str, Any]) -> bool:
    # The six Kubernetes match operators, e.g. {key: zone, operator: In, values: [a, b]}.
    # An unknown operator never matches.
    key = expression.get("key")
    operator = expression.get("operator")
    values = {str(value) for value in expression.get("values", []) or []}
    present = key in labels
    actual = str(labels.get(key, ""))
    if operator == "In":
        return present and actual in values
    if operator == "NotIn":
        return not present or actual not in values
    if operator == "Exists":
        return present
    if operator == "DoesNotExist":
        return not present
    if operator in {"Gt", "Lt"} and present and len(values) == 1:
        try:
            threshold = int(next(iter(values)))
            return int(actual) > threshold if operator == "Gt" else int(actual) < threshold
        except ValueError:
            return False
    return False


def matches_node_term(labels: dict[str, str], term: dict[str, Any]) -> bool:
    # Inside one term every expression must match (AND); between terms one is enough (OR, see below).
    expressions = term.get("matchExpressions", []) or []
    fields = term.get("matchFields", []) or []
    return all(matches_expression(labels, expr) for expr in [*expressions, *fields])


def node_label_view(node: dict[str, Any]) -> dict[str, str]:
    """Node labels plus metadata.name, so matchFields on metadata.name can be evaluated."""
    return {**(node.get("labels", {}) or {}), "metadata.name": node.get("name", "")}


def node_satisfies_required_placement(intent: Any, labels: dict[str, str]) -> bool:
    """nodeSelector AND (any required nodeSelectorTerm), as the Kubernetes scheduler applies them."""
    if any(labels.get(k) != v for k, v in node_selector(intent).items()):
        return False
    terms = required_node_terms(intent)
    return not terms or any(matches_node_term(labels, term) for term in terms)
