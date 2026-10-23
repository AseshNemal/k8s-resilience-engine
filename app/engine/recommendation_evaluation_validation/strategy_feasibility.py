"""Feasibility of the two strategies beyond a plain topology spread: required pod anti-affinity and a replica increase.

Neither has a solver of its own. Each builds a VIRTUAL copy of the deployment graph in which the proposed change is
already in the workload's declared intent, and asks the one constraint-aware search in ``feasibility_analyzer`` whether
a placement exists. Taints, capacity, node selectors, node and pod affinity, existing spread constraints and readiness
are therefore judged by exactly the same code, and the verdicts mean the same thing (FEASIBLE / INFEASIBLE /
INSUFFICIENT_EVIDENCE, never a bare yes). The original graph is only read.

POD_ANTI_AFFINITY. A required ``podAntiAffinity`` term on ``kubernetes.io/hostname`` selecting the service's own pods:
at most one replica per node. It admits a strict SUBSET of the placements a hostname spread (maxSkew 1) admits: one per
node implies a skew of at most one, but a spread also allows two on a node when there are more replicas than nodes. It
therefore never succeeds where the spread fails, and where both succeed they produce the same placement. It is not a
better placement, it is a harder guarantee, and it is offered for a Deployment that already declares SOFT anti-affinity
(``has_soft_self_anti_affinity``): preferred rules do not keep replicas apart, so hardening them is the minimal change
that matches what the owner already asked for. With more replicas than usable nodes it leaves replicas Pending, and the
search reports that as INFEASIBLE.

REPLICA_INCREASE. For a service that runs exactly one replica no change of placement can help, because losing the node
that hosts its only replica takes the service down. The change adds one replica AND the hostname spread constraint that
keeps the two apart; without the constraint the scheduler may put both on the same node. It is modelled by adding a
virtual replica co-located with the real one and asking whether the two can be placed on two different nodes, with the
real replica staying where it is whenever it can. The added replica is not "placement churn": it is not an existing pod
being recreated. It costs real CPU and memory (MEDIUM), which the capacity check already requires to be free.

REPLICA_INCREASE_ANTI_AFFINITY and REPLICA_INCREASE_PLUS_2 (added 2026-10-23, "more candidates"). The two variants of a replica increase for a
service that is concentrated on one node but is not necessarily a single replica: ONE more replica kept apart by a REQUIRED hostname
anti-affinity (a hard guarantee instead of a spread's skew of one, and it leaves a replica Pending when nodes run out), and TWO more replicas
kept apart by the hostname spread (a service that must survive more than the loss of one node's share, at twice the cost). Both are judged by
the same search as every other strategy, on a virtual copy with the added replicas co-located with the real one; neither is offered for a
Deployment that already has a hostname spread or required anti-affinity, because the patch would rewrite it.

What is not modelled (stated on each result): an autoscaler that owns the replica count, a PodDisruptionBudget, the rollout's
transient. Nothing here applies anything.
"""

import dataclasses
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    ASSUMPTIONS,
    NOT_MODELED,
    UNSCHEDULED_NODE,
    ConstraintFinding,
    FeasibilityResult,
    FeasibilityStatus,
    SchedulingEvidence,
    check_topology_spread_feasibility,
)
from app.engine.recommendation_evaluation_validation.scheduling_constraints import (
    required_pod_terms,
    selector_match_labels,
    service_matches,
)
from app.shared.schemas.deployment import DeploymentGraph, PodModel, ServicePlacement

HOSTNAME = "kubernetes.io/hostname"
INFEASIBLE, INSUFFICIENT, FEASIBLE = (FeasibilityStatus.INFEASIBLE, FeasibilityStatus.INSUFFICIENT_EVIDENCE,
                                      FeasibilityStatus.FEASIBLE)

# Changing how a strategy is modelled must change this, so an approval made under the old model is not read as one
# made under the new (it is part of ``scheduling_basis``).
ANTI_AFFINITY_MODEL = "pod-anti-affinity/required/kubernetes.io-hostname/self-selector/v1"
REPLICA_INCREASE_MODEL = "replica-increase/+1/with-hostname-spread/v1"
REPLICA_INCREASE_ANTI_AFFINITY_MODEL = "replica-increase/+1/with-required-anti-affinity/v1"
REPLICA_INCREASE_PLUS_2_MODEL = "replica-increase/+2/with-hostname-spread/v1"

# The name given to a replica the change would add. It is never a pod that exists.
ADDED_MARKER = "-added-"

ANTI_AFFINITY_ASSUMPTIONS = (
    "The proposal is modelled as a required podAntiAffinity term on kubernetes.io/hostname that selects the service's "
    "own pods: at most one replica per node.",
    *ASSUMPTIONS[1:],
)
REPLICA_INCREASE_ASSUMPTIONS = (
    "The proposal is modelled as one more replica together with a topologySpreadConstraint on kubernetes.io/hostname "
    "(maxSkew 1, whenUnsatisfiable DoNotSchedule) so the two replicas are kept on different nodes.",
    "The added replica is placed by a deterministic least-loaded prediction; the real scheduler may place it differently.",
    *ASSUMPTIONS[2:],
)
REPLICA_INCREASE_NOT_MODELED = (
    "a HorizontalPodAutoscaler or other controller that owns the replica count and would revert the change",
    "PodDisruptionBudgets and the quota the extra replica draws on",
    *NOT_MODELED,
)


def self_anti_affinity_term(selector: Mapping[str, str]) -> dict[str, Any]:
    """The required anti-affinity term the proposal adds: the service's own pods, one per node."""
    return {"labelSelector": {"matchLabels": dict(sorted(selector.items()))}, "topologyKey": HOSTNAME}


def has_hostname_spread(service: ServicePlacement) -> bool:
    intent = service.declared_intent
    return intent is not None and any(c.topology_key == HOSTNAME for c in intent.topology_spread_constraints)


def has_required_anti_affinity(service: ServicePlacement) -> bool:
    """Whether the service already carries any required pod anti-affinity term (the proposal would have to replace it)."""
    intent = service.declared_intent
    return intent is not None and bool(required_pod_terms(intent.pod_anti_affinity_rules))


def has_soft_self_anti_affinity(service: ServicePlacement) -> bool:
    """Whether the service already asks, softly, for its own replicas to avoid sharing a node.

    A ``preferredDuringSchedulingIgnoredDuringExecution`` term on the hostname key whose selector provably matches the
    service's own pods. It does not stop two replicas sharing a node; that is what the proposal would add. Judged only
    from the Deployment selector, so a term whose selector cannot be shown to match the service's own pods is not one.
    """
    intent = service.declared_intent
    if intent is None or not intent.selector:
        return False
    for weighted in (intent.pod_anti_affinity_rules or {}).get("preferredDuringSchedulingIgnoredDuringExecution") or []:
        term = weighted.get("podAffinityTerm") or {}
        labels = selector_match_labels(term.get("labelSelector"), term=term)
        if term.get("topologyKey") == HOSTNAME and labels is not None and service_matches(labels, intent.selector) is True:
            return True
    return False


def _service(deployment: DeploymentGraph, name: str) -> ServicePlacement | None:
    return next((item for item in deployment.services if item.name == name), None)


def _not_found(name: str) -> FeasibilityResult:
    return FeasibilityResult(status=INSUFFICIENT, reason=f"Service not found: {name}",
                             missing_evidence=(f"service {name} in the deployment graph",))


def check_pod_anti_affinity_feasibility(
    deployment: DeploymentGraph, service_name: str, evidence: SchedulingEvidence | None = None, **options: Any,
) -> FeasibilityResult:
    """Whether the ready replicas of the service could each be placed on a different node under a required
    hostname anti-affinity of their own, given every other verified constraint. The deployment is only read."""
    evidence = evidence if evidence is not None else SchedulingEvidence.from_graph(deployment)
    service = _service(deployment, service_name)
    if service is None:
        return _not_found(service_name)
    intent = service.declared_intent
    if intent is None or not intent.selector:
        return FeasibilityResult(
            status=INSUFFICIENT,
            reason=(f"The pod labels of {service_name} are not in the evidence, so an anti-affinity term cannot be "
                    f"written for its pods and its feasibility is undecided."),
            missing_evidence=(f"declared placement intent (pod labels) of service {service_name}",))
    if has_required_anti_affinity(service):
        return FeasibilityResult(
            status=INFEASIBLE,
            reason=(f"{service_name} already has required pod anti-affinity. Replacing it is a different change from "
                    f"the one that is modelled here."),
            findings=(ConstraintFinding("ALREADY_CONSTRAINED", INFEASIBLE, "existing required anti-affinity terms"),))

    virtual = deepcopy(deployment)
    twin = _service(virtual, service_name)
    twin.declared_intent.pod_anti_affinity_configured = True
    twin.declared_intent.pod_anti_affinity_rules = {      # the soft terms stay; the required one is what is added
        **twin.declared_intent.pod_anti_affinity_rules,
        "requiredDuringSchedulingIgnoredDuringExecution": [self_anti_affinity_term(twin.declared_intent.selector)]}
    result = check_topology_spread_feasibility(virtual, service_name, evidence, **options)

    if result.status is FEASIBLE:
        nodes = sorted(set(result.placement.values()))
        recreate = (f"{len(result.pods_to_recreate)} of {len(result.placement)} ready replica(s) have a different target "
                    f"node than today (placement churn)" if result.pods_to_recreate else
                    "No ready replica needs a different node")
        reason = (f"Under the modelled constraints the {len(result.placement)} ready replicas of {service_name} can each "
                  f"run on a different node ({', '.join(nodes)}), so no single node failure takes them all. This is a "
                  f"model prediction, not a scheduler guarantee. {recreate}. Adding the term does not move running pods; "
                  f"applying it edits the pod template, which rolls out every pod of the Deployment.")
    else:
        reason = f"Required pod anti-affinity (one replica per node): {result.reason}"
    return dataclasses.replace(result, reason=reason, assumptions=ANTI_AFFINITY_ASSUMPTIONS)


def check_replica_increase_feasibility(
    deployment: DeploymentGraph, service_name: str, evidence: SchedulingEvidence | None = None, *, added: int = 1,
    **options: Any,
) -> FeasibilityResult:
    """Whether adding ``added`` replica(s) to a single-replica service, kept apart by a hostname spread, can be placed
    so that no single node failure takes the service down. The deployment is only read."""
    evidence = evidence if evidence is not None else SchedulingEvidence.from_graph(deployment)
    service = _service(deployment, service_name)
    if service is None:
        return _not_found(service_name)
    ready = [pod for pod in service.pods if pod.ready and pod.node != UNSCHEDULED_NODE]
    if len(ready) != 1 or service.declared_replicas != 1:
        return FeasibilityResult(
            status=INFEASIBLE,
            reason=(f"A replica increase is proposed only for a service that declares and runs exactly one replica; "
                    f"{service_name} declares {service.declared_replicas} and has {len(ready)} ready, where a change "
                    f"of placement or repairing the unready replicas is the smaller change."),
            findings=(ConstraintFinding("REPLICA_INCREASE_NOT_NECESSARY", INFEASIBLE,
                                        f"{service.declared_replicas} declared, {len(ready)} ready"),))
    if has_hostname_spread(service) or has_required_anti_affinity(service):
        return FeasibilityResult(
            status=INFEASIBLE,
            reason=(f"{service_name} already has a hostname spread or required anti-affinity. The proposal would rewrite "
                    f"it, which is a different change from the one that is modelled here."),
            findings=(ConstraintFinding("ALREADY_CONSTRAINED", INFEASIBLE, "existing placement constraint"),))

    virtual = deepcopy(deployment)
    twin, real = _service(virtual, service_name), ready[0]
    names = [f"{service_name}{ADDED_MARKER}{i}" for i in range(1, added + 1)]
    for name in names:      # co-located with the real replica: the search decides where the new one may go
        twin.pods.append(PodModel(name=name, node=real.node, zone=real.zone, ready=True, phase="Running",
                                  namespace=real.namespace, owner_kind=real.owner_kind, owner_name=real.owner_name,
                                  owner_uid=real.owner_uid))
    twin.declared_replicas += added
    twin.ready_replicas += added
    if twin.declared_intent is not None:
        twin.declared_intent.declared_replicas += added
    result = check_topology_spread_feasibility(virtual, service_name, evidence, **options)

    if result.status is FEASIBLE:
        recreate = tuple(pod for pod in result.pods_to_recreate if ADDED_MARKER not in pod)
        placed = {name: node for name, node in result.placement.items() if name in names}
        reason = (f"Under the modelled constraints {service_name} can run {1 + added} replicas on different nodes: "
                  f"the existing replica on {', '.join(sorted({n for k, n in result.placement.items() if k not in names}))} "
                  f"and the added one on {', '.join(sorted(placed.values()))}, so no single node failure takes the "
                  f"service down. This is a model prediction, not a scheduler guarantee. The change adds {added} "
                  f"replica(s), which requests more CPU and memory, and edits the pod template, so the Deployment "
                  f"rolls out." + (f" {len(recreate)} existing replica(s) would also change node." if recreate else ""))
        return dataclasses.replace(result, reason=reason, pods_to_recreate=recreate,
                                   assumptions=REPLICA_INCREASE_ASSUMPTIONS, not_modeled=REPLICA_INCREASE_NOT_MODELED)
    return dataclasses.replace(
        result, reason=f"Adding a replica to {service_name} (with a hostname spread): {result.reason}",
        assumptions=REPLICA_INCREASE_ASSUMPTIONS, not_modeled=REPLICA_INCREASE_NOT_MODELED)


VARIANT_ASSUMPTIONS = {
    REPLICA_INCREASE_ANTI_AFFINITY_MODEL: (
        "The proposal is modelled as one more replica together with a required podAntiAffinity term on kubernetes.io/hostname that selects "
        "the service's own pods: at most one replica per node.",
        "The added replica is placed by a deterministic least-loaded prediction; the real scheduler may place it differently.",
        *ASSUMPTIONS[2:],
    ),
    REPLICA_INCREASE_PLUS_2_MODEL: (
        "The proposal is modelled as two more replicas together with a topologySpreadConstraint on kubernetes.io/hostname "
        "(maxSkew 1, whenUnsatisfiable DoNotSchedule) so the replicas are kept spread over the nodes.",
        "The added replicas are placed by a deterministic least-loaded prediction; the real scheduler may place them differently.",
        *ASSUMPTIONS[2:],
    ),
}


def check_replica_increase_variant_feasibility(
    deployment: DeploymentGraph, service_name: str, evidence: SchedulingEvidence | None = None, *, added: int,
    hard: bool, **options: Any,
) -> FeasibilityResult:
    """Whether adding ``added`` replica(s) to a concentrated service, kept apart by a required hostname anti-affinity (``hard``) or by
    a hostname spread, can be placed so that no single node failure takes the service down. The deployment is only read.

    Unlike ``check_replica_increase_feasibility`` the service may already run several replicas, as long as every declared replica is
    ready (a service with unready replicas has a smaller repair to make first)."""
    evidence = evidence if evidence is not None else SchedulingEvidence.from_graph(deployment)
    model = REPLICA_INCREASE_ANTI_AFFINITY_MODEL if hard else REPLICA_INCREASE_PLUS_2_MODEL
    assumptions = VARIANT_ASSUMPTIONS[model]
    service = _service(deployment, service_name)
    if service is None:
        return _not_found(service_name)
    ready = [pod for pod in service.pods if pod.ready and pod.node != UNSCHEDULED_NODE]
    if not ready or len(ready) != service.declared_replicas:
        return FeasibilityResult(
            status=INFEASIBLE,
            reason=(f"{service_name} declares {service.declared_replicas} replica(s) and has {len(ready)} ready and placed. Adding replicas "
                    f"is proposed only when every declared replica is ready, where repairing the unready ones is the smaller change."),
            findings=(ConstraintFinding("REPLICAS_NOT_SETTLED", INFEASIBLE,
                                        f"{service.declared_replicas} declared, {len(ready)} ready"),))
    if has_hostname_spread(service) or has_required_anti_affinity(service):
        return FeasibilityResult(
            status=INFEASIBLE,
            reason=(f"{service_name} already has a hostname spread or required anti-affinity. The proposal would rewrite it, which is a "
                    f"different change from the one that is modelled here."),
            findings=(ConstraintFinding("ALREADY_CONSTRAINED", INFEASIBLE, "existing placement constraint"),))
    intent = service.declared_intent
    if hard and (intent is None or not intent.selector):
        return FeasibilityResult(
            status=INSUFFICIENT,
            reason=(f"The pod labels of {service_name} are not in the evidence, so an anti-affinity term cannot be written for its pods "
                    f"and its feasibility is undecided."),
            missing_evidence=(f"declared placement intent (pod labels) of service {service_name}",))

    virtual = deepcopy(deployment)
    twin, real = _service(virtual, service_name), ready[0]
    names = [f"{service_name}{ADDED_MARKER}{i}" for i in range(1, added + 1)]
    for name in names:      # co-located with a real replica: the search decides where each may go
        twin.pods.append(PodModel(name=name, node=real.node, zone=real.zone, ready=True, phase="Running",
                                  namespace=real.namespace, owner_kind=real.owner_kind, owner_name=real.owner_name,
                                  owner_uid=real.owner_uid))
    twin.declared_replicas += added
    twin.ready_replicas += added
    if twin.declared_intent is not None:
        twin.declared_intent.declared_replicas += added
    if hard:
        twin.declared_intent.pod_anti_affinity_configured = True
        twin.declared_intent.pod_anti_affinity_rules = {
            **twin.declared_intent.pod_anti_affinity_rules,
            "requiredDuringSchedulingIgnoredDuringExecution": [self_anti_affinity_term(twin.declared_intent.selector)]}
    result = check_topology_spread_feasibility(virtual, service_name, evidence, **options)
    label = "a required hostname anti-affinity" if hard else "a hostname spread"

    if result.status is FEASIBLE:
        recreate = tuple(pod for pod in result.pods_to_recreate if ADDED_MARKER not in pod)
        hosting = sorted(set(result.placement.values()))
        reason = (f"Under the modelled constraints {service_name} can run {len(ready) + added} replicas ({added} added) over "
                  f"{len(hosting)} node(s) ({', '.join(hosting)}) with {label}, so no single node failure takes the service down. "
                  f"This is a model prediction, not a scheduler guarantee. The change adds {added} replica(s), which request more CPU "
                  f"and memory, and edits the pod template, so the Deployment rolls out."
                  + (f" {len(recreate)} existing replica(s) would also change node." if recreate else ""))
        return dataclasses.replace(result, reason=reason, pods_to_recreate=recreate, assumptions=assumptions,
                                   not_modeled=REPLICA_INCREASE_NOT_MODELED)
    return dataclasses.replace(
        result, reason=f"Adding {added} replica(s) to {service_name} with {label}: {result.reason}",
        assumptions=assumptions, not_modeled=REPLICA_INCREASE_NOT_MODELED)


def check_replica_increase_anti_affinity_feasibility(deployment: DeploymentGraph, service_name: str,
                                                     evidence: SchedulingEvidence | None = None, **options: Any) -> FeasibilityResult:
    return check_replica_increase_variant_feasibility(deployment, service_name, evidence, added=1, hard=True, **options)


def check_replica_increase_plus_two_feasibility(deployment: DeploymentGraph, service_name: str,
                                                evidence: SchedulingEvidence | None = None, **options: Any) -> FeasibilityResult:
    return check_replica_increase_variant_feasibility(deployment, service_name, evidence, added=2, hard=False, **options)
