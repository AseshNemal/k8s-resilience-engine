"""Single definition of Component 2's placement metrics.

Every module (live graph, drift analysis, Baseline B2, scenario experiments) must use these
functions so that one metric never has two meanings.

Definitions and sources:
- Failure domain: the worker node (``kubernetes.io/hostname``) - Study Guide section 9 Step 5.
- Ready replica: a pod whose Ready condition is true and that is bound to a node.
- Concentrated: all ready replicas of a service share one failure domain - TAF p.7.
- Exposed failure domain: a node whose failure leaves the service with 0 ready replicas,
  i.e. SA(s, {n}) = 0 - proposal section 7.4, Study Guide section 9 Step 6.
- Exposure rate: exposed services / evaluated services - Study Guide section 12.
- Placement drift rate: share of services whose exposure status changes - Study Guide section 12.
- Constraint compliance rate: share of services whose actual placement satisfies their declared
  topology or affinity rules - Study Guide section 12.

With the node as failure domain, "concentrated" and "exposed" are the same condition for any
service with at least one ready replica: all ready replicas on one node <=> losing that node
leaves zero. They are kept as separate functions because they answer different questions and
would differ for a zone-level failure domain.
"""
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExposureStatus(str, Enum):
    EXPOSED = "EXPOSED"
    NOT_EXPOSED = "NOT_EXPOSED"
    # The service has no ready replica before any failure, so a node failure cannot remove it.
    UNAVAILABLE = "UNAVAILABLE"


def pod_is_ready(pod: Mapping[str, Any]) -> bool:
    return bool(pod.get("is_ready", pod.get("ready", False)))


def pod_node(pod: Mapping[str, Any]) -> str | None:
    return pod.get("node_name") or pod.get("node") or None


def ready_replicas_per_node(pods: Iterable[Mapping[str, Any]]) -> Counter[str]:
    # e.g. {"worker": 2, "worker2": 1}. Pods that are not ready or have no node yet are not counted.
    return Counter(
        node for pod in pods
        if pod_is_ready(pod) and (node := pod_node(pod)) is not None
    )


def ready_replica_count(pods: Iterable[Mapping[str, Any]]) -> int:
    return sum(ready_replicas_per_node(pods).values())


def exposed_failure_domains(pods: Iterable[Mapping[str, Any]]) -> list[str]:
    """Nodes n with SA(s, {n}) = 0. Under single-node failure this has at most one element."""
    per_node = ready_replicas_per_node(pods)
    # If every ready copy is on one node, losing that node leaves nothing. Two or more nodes: no single node does.
    return sorted(per_node) if len(per_node) == 1 else []


def is_concentrated(pods: Iterable[Mapping[str, Any]]) -> bool | None:
    """None when the service has no ready replica (concentration is undefined)."""
    per_node = ready_replicas_per_node(pods)
    if not per_node:
        return None
    return len(per_node) == 1


def concentration_ratio(pods: Iterable[Mapping[str, Any]]) -> float | None:
    """Max ready replicas on one node / total ready replicas (PROJECT_CONTEXT section 15)."""
    per_node = ready_replicas_per_node(pods)
    total = sum(per_node.values())
    # No ready copies: the ratio is undefined, so None rather than a made-up 0.
    if total == 0:
        return None
    # e.g. 2 of 3 copies on the busiest node -> 0.67. 1.0 means everything on one node.
    return max(per_node.values()) / total


def exposure_status(pods: Iterable[Mapping[str, Any]]) -> ExposureStatus:
    pods = list(pods)
    # Already down before any failure: a node failure can't take it down, so it is not "exposed".
    if ready_replica_count(pods) == 0:
        return ExposureStatus.UNAVAILABLE
    return ExposureStatus.EXPOSED if exposed_failure_domains(pods) else ExposureStatus.NOT_EXPOSED


def exposure_rate(statuses: Iterable[ExposureStatus]) -> float | None:
    """Exposed / evaluated services, where UNAVAILABLE services are not evaluated.

    Returns None when no service can be evaluated, instead of inventing 0.0 or 1.0.
    """
    evaluated = [s for s in statuses if s != ExposureStatus.UNAVAILABLE]
    if not evaluated:
        return None
    return sum(s == ExposureStatus.EXPOSED for s in evaluated) / len(evaluated)


def compliance_rate(results: Mapping[str, bool | None]) -> float | None:
    """results maps service -> True (rules satisfied), False (violated), None (no rules declared).

    Services without declared topology/affinity rules are excluded from the denominator.
    """
    judged = [ok for ok in results.values() if ok is not None]
    if not judged:
        return None
    return sum(judged) / len(judged)


@dataclass(frozen=True)
class PlacementDriftResult:
    compared_services: list[str]
    changed_services: list[str]
    added_services: list[str]
    removed_services: list[str]
    transitions: dict[str, str] = field(default_factory=dict)

    @property
    def rate(self) -> float | None:
        # D_rate = services whose exposure status changed / services compared
        if not self.compared_services:
            return None
        return len(self.changed_services) / len(self.compared_services)


def placement_drift(
    before: Mapping[str, ExposureStatus],
    after: Mapping[str, ExposureStatus],
) -> PlacementDriftResult:
    """Compares exposure status per service between two snapshots.

    Only services present in both snapshots are compared; services that appear or disappear
    are reported separately and are not counted in the rate.
    """
    # Only services seen at both T0 and T1 are compared; new or removed services are listed separately.
    compared = sorted(set(before) & set(after))
    changed = [s for s in compared if before[s] != after[s]]
    return PlacementDriftResult(
        compared_services=compared,
        changed_services=changed,
        added_services=sorted(set(after) - set(before)),
        removed_services=sorted(set(before) - set(after)),
        transitions={s: f"{before[s].value} -> {after[s].value}" for s in changed},
    )
