"""Kubernetes scheduling predicates over the evidence a DeploymentGraph carries.

Every predicate that depends on a fact the graph may not hold returns ``True`` (satisfied), ``False``
(verified violated) or ``None`` (cannot be told from the evidence). Callers must treat ``None`` as unknown,
never as satisfied: that is what keeps an unverified placement from being reported as feasible.

The functions are pure and know nothing about the graph's classes; ``feasibility_analyzer`` decides what to
do with the answers. Only the semantics of the Kubernetes scheduler's hard (Required / NoSchedule) filters
are modelled here. Soft rules (preferred affinity, PreferNoSchedule, ScheduleAnyway) never block a pod.
"""

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

HARD_TAINT_EFFECTS = frozenset({"NoSchedule", "NoExecute"})

# Kubernetes resource quantities: a number plus an optional binary (Ki..Ei), decimal (k..E) or milli suffix.
# Member 2's parser lives inside its own package, which this component must not reach into, so this small
# parser is local; it returns CPU in cores and memory in GiB, the units the DeploymentGraph uses for nodes.
_QUANTITY = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([A-Za-z]*)")
_SUFFIX = {
    "n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18,
    "Ki": 2.0 ** 10, "Mi": 2.0 ** 20, "Gi": 2.0 ** 30, "Ti": 2.0 ** 40, "Pi": 2.0 ** 50, "Ei": 2.0 ** 60,
}
_GIB = 2.0 ** 30


def _quantity(value: Any) -> float:
    match = _QUANTITY.fullmatch(str(value).strip())
    if match is None or match.group(2) not in _SUFFIX:
        raise ValueError(f"not a Kubernetes quantity: {value!r}")
    return float(match.group(1)) * _SUFFIX[match.group(2)]


def pod_requests(per_container: Sequence[Mapping[str, str]]) -> tuple[float, float]:
    """(CPU cores, memory GiB) a pod asks for: the sum of its containers' requests.

    A container with no request asks for 0, which is what the scheduler assumes. Init containers and pod
    overhead are not in the evidence, so the result is a lower bound for pods that have them.
    Raises ValueError for a request that is not a quantity.
    """
    cpu = sum(_quantity(c["cpu"]) for c in per_container if c.get("cpu") is not None)
    memory = sum(_quantity(c["memory"]) for c in per_container if c.get("memory") is not None)
    return cpu, memory / _GIB


def remaining_slots(available: float | None, request: float) -> float | None:
    """How many requests of this size fit in what is left; None when the remainder is unknown."""
    if request <= 0:
        return math.inf
    if available is None:
        return None
    return math.floor(available / request + 1e-9)


# --- labels, node selectors and node affinity ------------------------------------------------------

def _all(verdicts: Sequence[bool | None]) -> bool | None:
    if any(v is False for v in verdicts):
        return False
    return None if any(v is None for v in verdicts) else True


def _any(verdicts: Sequence[bool | None]) -> bool | None:
    if any(v is True for v in verdicts):
        return True
    return None if any(v is None for v in verdicts) else False


def match_expression(labels: Mapping[str, str], expression: Mapping[str, Any]) -> bool | None:
    """One matchExpressions / matchFields requirement against a label map."""
    key, operator = expression.get("key"), expression.get("operator")
    values = expression.get("values") or []
    present = key in labels
    if operator == "In":
        return present and labels[key] in values
    if operator == "NotIn":
        return not present or labels[key] not in values
    if operator == "Exists":
        return present
    if operator == "DoesNotExist":
        return not present
    if operator in ("Gt", "Lt"):
        try:
            bound = int(values[0])
        except (IndexError, TypeError, ValueError):
            return None
        if not present:
            return False
        try:
            actual = int(labels[key])
        except ValueError:
            return False
        return actual > bound if operator == "Gt" else actual < bound
    return None


def node_selector_matches(node_labels: Mapping[str, str], selector: Mapping[str, str]) -> bool | None:
    """spec.nodeSelector against the node's labels. A node with no labels at all was not read: every real
    node carries at least kubernetes.io/hostname."""
    if not selector:
        return True
    if not node_labels:
        return None
    return all(node_labels.get(key) == value for key, value in selector.items())


def node_affinity_matches(node_name: str, node_labels: Mapping[str, str], rules: Mapping[str, Any]) -> bool | None:
    """requiredDuringSchedulingIgnoredDuringExecution node affinity: terms are ORed, requirements in a term
    are ANDed. An empty term matches nothing. Preferred rules are ignored."""
    terms = ((rules or {}).get("requiredDuringSchedulingIgnoredDuringExecution") or {}).get("nodeSelectorTerms")
    if not terms:
        return True
    return _any([_node_term_matches(node_name, node_labels, term) for term in terms])


def _node_term_matches(node_name: str, node_labels: Mapping[str, str], term: Mapping[str, Any]) -> bool | None:
    expressions, fields = term.get("matchExpressions") or [], term.get("matchFields") or []
    if not expressions and not fields:
        return False
    verdicts: list[bool | None] = [
        match_expression(node_labels, e) if node_labels else None for e in expressions
    ]
    for field in fields:
        verdicts.append(
            match_expression({"metadata.name": node_name}, field) if field.get("key") == "metadata.name" else None
        )
    return _all(verdicts)


# --- taints and tolerations ------------------------------------------------------------------------

def _tolerates(toleration: Mapping[str, Any], taint: Mapping[str, Any]) -> bool:
    if toleration.get("effect") and toleration["effect"] != taint.get("effect"):
        return False
    operator = toleration.get("operator") or "Equal"
    if not toleration.get("key"):
        return operator == "Exists"  # an empty key with Exists tolerates every taint
    if toleration["key"] != taint.get("key"):
        return False
    return operator == "Exists" or (toleration.get("value") or "") == (taint.get("value") or "")


def untolerated_taints(
    taints: Sequence[Mapping[str, Any]], tolerations: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """The taints that stop a pod being scheduled onto the node (NoSchedule / NoExecute, not tolerated)."""
    return [
        taint for taint in taints
        if taint.get("effect") in HARD_TAINT_EFFECTS and not any(_tolerates(t, taint) for t in tolerations)
    ]


# --- pod (anti-)affinity and spread selectors ------------------------------------------------------

def required_pod_terms(rules: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The hard pod (anti-)affinity terms; the preferred ones never block scheduling."""
    return list((rules or {}).get("requiredDuringSchedulingIgnoredDuringExecution") or [])


def selector_match_labels(selector: Mapping[str, Any] | None, *, term: Mapping[str, Any] | None = None) -> dict[str, str] | None:
    """The matchLabels of a label selector, or None when the selector cannot be evaluated from the evidence
    (matchExpressions, an empty or missing selector, or a term that reaches other namespaces)."""
    if term is not None and (term.get("namespaces") or term.get("namespaceSelector")):
        return None
    if not selector or selector.get("matchExpressions") or not selector.get("matchLabels"):
        return None
    return {str(k): str(v) for k, v in selector["matchLabels"].items()}


def service_matches(match_labels: Mapping[str, str], service_selector: Mapping[str, str] | None) -> bool | None:
    """Whether a service's pods carry the given labels, judged from the service's own Deployment selector.

    Every pod of the service carries at least the labels of that selector, so a selector that contains the
    wanted labels proves a match and one that contradicts a wanted label proves a mismatch. Anything else
    depends on pod labels the graph does not hold.
    """
    if not service_selector:
        return None
    if all(service_selector.get(key) == value for key, value in match_labels.items()):
        return True
    if any(key in service_selector and service_selector[key] != value for key, value in match_labels.items()):
        return False
    return None
