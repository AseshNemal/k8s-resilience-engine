"""Constraint-aware feasibility of a TOPOLOGY_SPREAD recommendation.

The question is whether the ready replicas of one service could be re-placed so that no single worker-node
failure takes them all, given what is known about the nodes and about the workload's declared placement
intent. It is answered with one of three statuses, never a bare yes:

* ``FEASIBLE``: every constraint the model covers was verified and a placement exists;
* ``INFEASIBLE``: a verified constraint prevents the proposal, even if everything unverified is assumed
  to be favourable;
* ``INSUFFICIENT_EVIDENCE``: a placement exists only if facts the evidence does not hold turn out well
  (node taints, labels, capacity, or the workload's own scheduling rules).

Scheduling feasibility and resilience are reported separately: a spread constraint on a single replica is
schedulable, yet it cannot make that service survive the loss of the node hosting its only replica.

The proposed change is modelled as ``topologySpreadConstraints`` on ``kubernetes.io/hostname`` with
``maxSkew`` (default 1) and ``whenUnsatisfiable: DoNotSchedule``. Replicas are placed one at a time, trying
the node with the fewest of them first and backtracking on a dead end, until every ready replica is placed on
at least two different nodes. A placement found that way is a prediction of what the scheduler could do, not
a promise of what it will do. ``INFEASIBLE`` is returned only after the whole search space has been ruled
out; if the search budget runs out first the answer is inconclusive (``INSUFFICIENT_EVIDENCE``), never a
guessed ``INFEASIBLE``. ``NOT_MODELED`` lists what the model ignores.

Adding a topology spread constraint does not move pods that are already running: it only filters where
*newly scheduled* pods may go. The placement returned is therefore a target state, reachable only by
recreating the pods listed in ``pods_to_recreate`` (a rollout restart, or deleting them).

Two policy fields of a spread constraint are not in the contract: ``nodeTaintsPolicy`` (whether nodes with
untolerated taints, or NotReady or cordoned nodes, still count as domains; the default is that they do) and
``nodeAffinityPolicy``. Where the answer depends on them it is inconclusive rather than decided on a default.

All the facts come from the DeploymentGraph (Member 2's contract), which is read and never changed. Node
taints are not in that contract: they are taken from a ``SchedulingEvidence`` the caller supplies, and are
reported as unknown when nobody supplies them.

Upstream evidence gaps (proposals for the owners; nothing here changes their code or the shared schemas).
Until they are closed, a live candidate is INSUFFICIENT_EVIDENCE rather than FEASIBLE:

1. ``NodeModel`` has no taints. Needed: ``taints: list[{key, value, effect}] | None`` where ``None`` means
   "not read" and ``[]`` means "none". Neither the REST client's ``normalize_node`` nor ``nodes_from_objects``
   reads ``spec.taints``.
2. ``NodeModel.schedulable`` defaults to ``True``, so an unread node looks uncordoned. Needed: ``bool | None``
   or an explicit "status observed" flag. Meanwhile ``ready is not None`` is the only sign a node's status was
   read, and ``schedulable=True`` and the capacity figures are trusted only then.
3. ``discover_live`` (what the live pipeline receives) builds nodes through the REST client: ``labels={}``,
   ``ready=None`` and ``cpu_available`` as raw allocatable rather than what is left; and it attaches no
   ``declared_intent`` to services. The per-namespace path (``collect_live_cluster``) carries ready,
   schedulable, labels, remaining capacity and intent. Needed on the discovery path: the same.
4. ``PodModel`` has no labels, so pod (anti-)affinity and spread selectors are judged from each service's
   Deployment selector; a service without intent leaves every term that might match it undecidable.
5. When several Deployments back one service the intent keeps only the first; the pod template is then unknown.
"""

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.engine.recommendation_evaluation_validation.scheduling_constraints import (
    node_affinity_matches,
    node_selector_matches,
    pod_requests,
    remaining_slots,
    required_pod_terms,
    selector_match_labels,
    service_matches,
    untolerated_taints,
)
from app.shared.schemas.deployment import DeploymentGraph, NodeModel, PodModel, ServicePlacement

# The graph builder gives a pod the scheduler has not placed this node name.
UNSCHEDULED_NODE = "unscheduled"
DEFAULT_MAX_SKEW = 1
# How many partial placements the search may expand per question before it gives up (and says so).
DEFAULT_SEARCH_BUDGET = 20_000

ASSUMPTIONS = (
    "The proposal is modelled as a topologySpreadConstraint on kubernetes.io/hostname with "
    "whenUnsatisfiable=DoNotSchedule.",
    "Placement is a deterministic least-loaded prediction; the real scheduler may place pods differently.",
    "A node record whose Ready condition is absent is treated as unread: its schedulable flag defaults to "
    "True in the contract and so proves nothing.",
    "The placement is a target state. A spread constraint does not move running pods; it is reached only by "
    "recreating the pods in pods_to_recreate, and a RollingUpdate's transient (old pods still counted for "
    "skew and capacity while new ones schedule) is not modelled.",
)

NOT_MODELED = (
    "init-container and pod-overhead requests (the pod request is a lower bound)",
    "host ports, the kubelet's maximum pods per node, and volume topology (PersistentVolume node affinity); "
    "the evidence carries none of them, so any of them can still block a placement judged feasible here",
    "ResourceQuota, LimitRange, priority and preemption, custom schedulers, PodDisruptionBudgets",
    "soft rules: preferred affinity, PreferNoSchedule taints, ScheduleAnyway spread constraints",
    "pod (anti-)affinity terms with matchExpressions, namespaces or namespaceSelector",
    "pods of other namespaces",
)


class FeasibilityStatus(str, Enum):
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


FEASIBLE = FeasibilityStatus.FEASIBLE
INFEASIBLE = FeasibilityStatus.INFEASIBLE
INSUFFICIENT = FeasibilityStatus.INSUFFICIENT_EVIDENCE


@dataclass(frozen=True)
class ConstraintFinding:
    """One verified blocker: which constraint, where, and why."""

    constraint: str
    status: FeasibilityStatus
    detail: str
    node: str | None = None


@dataclass(frozen=True)
class SchedulingEvidence:
    """Scheduling facts the DeploymentGraph contract cannot carry.

    ``node_taints`` maps a node name to its taints (``{"key", "value", "effect"}``). A node missing from the
    map has unread taints, which is different from a node with ``[]``, which has none. NodeModel has no
    taints field today; ``from_graph`` picks the field up if the contract ever gains one.
    """

    node_taints: Mapping[str, Sequence[Mapping[str, Any]]] = field(default_factory=dict)

    @classmethod
    def from_graph(cls, deployment: DeploymentGraph) -> "SchedulingEvidence":
        taints = {}
        for node in deployment.nodes:
            observed = getattr(node, "taints", None)
            if observed is not None:
                taints[node.name] = observed
        return cls(node_taints=taints)


@dataclass(frozen=True)
class FeasibilityResult:
    status: FeasibilityStatus
    reason: str
    # Can the modelled placement be scheduled at all? None when it was not evaluated.
    scheduling: FeasibilityStatus | None = None
    # Would the placement survive the loss of any one node? None when the evidence cannot say.
    single_node_resilience: bool | None = None
    findings: tuple[ConstraintFinding, ...] = ()
    # Predicted pod -> node placement; filled only when the status is FEASIBLE.
    placement: Mapping[str, str] = field(default_factory=dict)
    missing_evidence: tuple[str, ...] = ()
    # The pods that would have to be recreated to reach `placement`; the constraint alone moves none of them.
    pods_to_recreate: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ASSUMPTIONS
    not_modeled: tuple[str, ...] = NOT_MODELED

    @property
    def feasible(self) -> bool:
        """True only for a verified FEASIBLE; INSUFFICIENT_EVIDENCE is never reported as feasible."""
        return self.status is FEASIBLE

    @property
    def requires_pod_recreation(self) -> bool:
        return bool(self.pods_to_recreate)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "scheduling": self.scheduling.value if self.scheduling else None,
            "singleNodeResilience": self.single_node_resilience,
            "findings": [
                {"constraint": f.constraint, "status": f.status.value, "detail": f.detail, "node": f.node}
                for f in self.findings
            ],
            "missingEvidence": list(self.missing_evidence),
            "placement": dict(self.placement),
            "podsToRecreate": list(self.pods_to_recreate),
            "requiresPodRecreation": self.requires_pod_recreation,
            "assumptions": list(self.assumptions),
            "notModeled": list(self.not_modeled),
        }


@dataclass
class _NodeState:
    node: NodeModel
    blockers: list[ConstraintFinding]
    # Is the node a topology-spread domain (does it pass nodeSelector and required node affinity)?
    domain: bool | None
    # Most replicas of the service the node can hold; infinite when unconstrained or unknown.
    cap: float


@dataclass
class _Spread:
    """An existing hard (DoNotSchedule) topologySpreadConstraint of the service."""

    key: str
    max_skew: int
    min_domains: int | None
    others: Counter  # node -> pods of other services the constraint's selector provably matches


def _label(node: NodeModel, key: str) -> str | None:
    return node.labels.get(key) if node.labels else None


def _selector_of(service: ServicePlacement) -> Mapping[str, str] | None:
    """The labels every pod of the service carries, when the intent is attributable to one Deployment."""
    intent = service.declared_intent
    return intent.selector if intent is not None and len(service.deployment_names) <= 1 else None


class _Context:
    """The service-level facts, and what could not be established about them."""

    def __init__(self, deployment: DeploymentGraph, service: ServicePlacement,
                 evidence: SchedulingEvidence, max_skew: int, movable: Sequence[PodModel]) -> None:
        self.deployment, self.service, self.evidence, self.max_skew = deployment, service, evidence, max_skew
        self.nodes = {node.name: node for node in deployment.nodes}
        self._missing: dict[str, set[str]] = {}
        self.intent = self._usable_intent()
        self.request = self._request()
        self.self_anti_keys: list[str] = []
        self.anti_blocked: list[tuple[str, set[str], str]] = []
        self.affinity_allowed: list[tuple[str, set[str], str]] = []
        self.spreads: list[_Spread] = []
        self._check_workload_kind(movable)
        if self.intent is not None:
            self._read_rules()

    def need(self, what: str, node: str | None = None) -> None:
        self._missing.setdefault(what, set())
        if node is not None:
            self._missing[what].add(node)

    def missing_evidence(self) -> tuple[str, ...]:
        return tuple(f"{what}: {', '.join(sorted(nodes))}" if nodes else what
                     for what, nodes in sorted(self._missing.items()))

    # -- the workload's declared intent ---------------------------------------------------------

    def _usable_intent(self):
        name, intent = self.service.name, self.service.declared_intent
        if intent is None:
            self.need(f"declared placement intent (nodeSelector, tolerations, affinity, spread constraints, "
                      f"resource requests) of service {name}")
            return None
        if len(self.service.deployment_names) > 1:
            self.need(f"pod template of service {name}: it is backed by several Deployments "
                      f"({', '.join(self.service.deployment_names)}) and the declared intent keeps only one")
            return None
        return intent

    def _check_workload_kind(self, movable: Sequence[PodModel]) -> None:
        """The proposal is a spread constraint on a Deployment's pod template."""
        name, kinds = self.service.name, {pod.owner_kind for pod in movable}
        if None in kinds:
            self.need(f"owner kind of the pods of service {name} (to tell whether a Deployment manages them)")
        for kind in sorted(k for k in kinds if k not in (None, "ReplicaSet")):
            self.need(f"workload kind {kind} of service {name}: the proposal is modelled for Deployments only")

    def _request(self) -> tuple[float, float] | None:
        if self.intent is None:
            return None
        if not self.intent.resource_requests:
            self.need(f"resource requests of service {self.service.name} (no containers captured)")
            return None
        others = sorted({key for container in self.intent.resource_requests for key in container
                         if key not in ("cpu", "memory")})
        if others:  # nodes report only CPU and memory, so these cannot be checked against any node
            self.need(f"node capacity for {', '.join(others)} requested by service {self.service.name} "
                      f"(the nodes report only CPU and memory)")
        try:
            return pod_requests(self.intent.resource_requests)
        except (ValueError, KeyError, TypeError):
            self.need(f"resource requests of service {self.service.name} could not be parsed")
            return None

    def _read_rules(self) -> None:
        intent, name = self.intent, self.service.name
        for flagged, rules, what in (
            (intent.node_affinity_configured, intent.node_affinity_rules, "node affinity"),
            (intent.pod_anti_affinity_configured, intent.pod_anti_affinity_rules, "pod anti-affinity"),
            (intent.pod_affinity_configured, intent.pod_affinity_rules, "pod affinity"),
        ):
            if flagged and not rules:
                self.need(f"{what} rules of service {name} (flagged but not captured)")
        for term in required_pod_terms(intent.pod_anti_affinity_rules):
            self._anti_affinity(term)
        for term in required_pod_terms(intent.pod_affinity_rules):
            self._affinity(term)
        for constraint in intent.topology_spread_constraints:
            self._spread(constraint)

    def _term(self, term: Mapping[str, Any], what: str):
        labels, key = selector_match_labels(term.get("labelSelector"), term=term), term.get("topologyKey")
        if labels is None or not key:
            self.need(f"{what} term of service {self.service.name} (only matchLabels selectors within the "
                      f"namespace can be evaluated)")
            return None
        return labels, key, service_matches(labels, _selector_of(self.service))

    def _matching(self, labels: Mapping[str, str], what: str) -> tuple[list[ServicePlacement], bool]:
        """Other services whose pods provably match the labels, and whether any service was undecidable."""
        matched, unknown = [], False
        for other in self.deployment.services:
            if other.name == self.service.name or not other.pods:
                continue
            verdict = service_matches(labels, _selector_of(other))
            if verdict is None:
                unknown = True
                self.need(f"pod labels of service {other.name} (needed to evaluate {what} of "
                          f"{self.service.name})")
            elif verdict:
                matched.append(other)
        return matched, unknown

    def _domains_of(self, services: Sequence[ServicePlacement], key: str) -> set[str]:
        values: set[str] = set()
        for pod in (p for s in services for p in s.pods):
            node = self.nodes.get(pod.node)
            if node is None:
                if key != "kubernetes.io/hostname":
                    self.need(f"node labels of {pod.node}, which hosts a pod the rule refers to")
            elif _label(node, key) is None:
                self.need("node labels", node.name)
            else:
                values.add(_label(node, key))
        return values

    def _anti_affinity(self, term: Mapping[str, Any]) -> None:
        parsed = self._term(term, "pod anti-affinity")
        if parsed is None:
            return
        labels, key, own = parsed
        if own is None:
            self.need(f"pod labels of service {self.service.name} (to tell whether its own anti-affinity "
                      f"term matches its pods)")
        elif own:
            self.self_anti_keys.append(key)
        matched, _ = self._matching(labels, "pod anti-affinity")
        if matched:
            self.anti_blocked.append((key, self._domains_of(matched, key), ", ".join(s.name for s in matched)))

    def _affinity(self, term: Mapping[str, Any]) -> None:
        parsed = self._term(term, "pod affinity")
        if parsed is None:
            return
        labels, key, own = parsed
        if own is not False:
            self.need(f"pod affinity of service {self.service.name} that its own pods satisfy or may satisfy "
                      f"(not modelled)")
            return
        matched, unknown = self._matching(labels, "pod affinity")
        if not unknown:
            self.affinity_allowed.append(
                (key, self._domains_of(matched, key), ", ".join(s.name for s in matched) or "no service"))

    def _spread(self, constraint) -> None:
        name = self.service.name
        if constraint.when_unsatisfiable == "ScheduleAnyway":
            return  # a soft constraint never blocks a pod
        if (constraint.when_unsatisfiable != "DoNotSchedule" or constraint.max_skew is None
                or not constraint.topology_key):
            self.need(f"spread constraint of service {name} is incomplete (maxSkew, topologyKey or "
                      f"whenUnsatisfiable missing)")
            return
        labels = selector_match_labels(constraint.label_selector)
        if labels is None:
            self.need(f"spread constraint selector of service {name} cannot be evaluated (only matchLabels)")
            return
        if service_matches(labels, _selector_of(self.service)) is not True:
            self.need(f"spread constraint of service {name} whose selector does not provably match its own pods")
            return
        matched, unknown = self._matching(labels, "spread constraint")
        if not unknown:
            self.spreads.append(_Spread(
                constraint.topology_key, constraint.max_skew, constraint.min_domains,
                Counter(pod.node for s in matched for pod in s.pods)))

    # -- one node -------------------------------------------------------------------------------

    def assess(self, node: NodeModel, resident: int) -> _NodeState:
        blockers: list[ConstraintFinding] = []

        def block(constraint: str, detail: str) -> None:
            blockers.append(ConstraintFinding(constraint, INFEASIBLE, detail, node.name))

        if node.ready is False:
            block("NODE_READY", f"{node.name} is NotReady")
        if node.schedulable is False:
            block("NODE_SCHEDULABLE", f"{node.name} is cordoned (spec.unschedulable)")
        if node.ready is None:
            self.need("node readiness and schedulability were not reported", node.name)
        intent, domain, cap = self.intent, None, math.inf
        if intent is not None:
            selector = node_selector_matches(node.labels, intent.node_selector)
            affinity = node_affinity_matches(node.name, node.labels, intent.node_affinity_rules)
            if selector is False:
                block("NODE_SELECTOR", f"labels of {node.name} do not satisfy nodeSelector {intent.node_selector}")
            if affinity is False:
                block("NODE_AFFINITY", f"{node.name} does not satisfy the required node affinity")
            if selector is None or affinity is None:
                self.need("node labels", node.name)
            domain = False if False in (selector, affinity) else (None if None in (selector, affinity) else True)
        self._check_taints(node, block)
        if self.request is not None:
            cap = self._check_capacity(node, resident, block)
        self._check_pod_rules(node, block)
        return _NodeState(node, blockers, domain, cap)

    def _check_taints(self, node: NodeModel, block) -> None:
        taints = self.evidence.node_taints.get(node.name)
        if taints is None:
            self.need("node taints (the NodeModel contract carries none)", node.name)
        elif self.intent is not None:
            untolerated = untolerated_taints(taints, self.intent.tolerations)
            if untolerated:
                block("NODE_TAINT", f"{node.name} has taint {untolerated[0].get('key')}="
                                    f"{untolerated[0].get('value')}:{untolerated[0].get('effect')} "
                                    f"that the workload does not tolerate")

    def _check_capacity(self, node: NodeModel, resident: int, block) -> float:
        cpu, memory = self.request
        slots = [remaining_slots(node.cpu_available, cpu), remaining_slots(node.memory_available, memory)]
        if None in slots:
            self.need("node capacity (CPU/memory available)", node.name)
            return math.inf
        # The replicas already on the node are inside what it has used, so it can hold them plus what is free.
        cap = resident + min(slots)
        if cap < 1:
            block("NODE_CAPACITY", f"{node.name} has {node.cpu_available} CPU and {node.memory_available} GiB "
                                   f"free, less than one replica asks for ({cpu:g} CPU, {memory:.3g} GiB)")
        return cap

    def _check_pod_rules(self, node: NodeModel, block) -> None:
        for key, blocked, others in self.anti_blocked:
            if _label(node, key) in blocked:
                block("POD_ANTI_AFFINITY", f"{node.name} shares {key} with a pod of {others}, which the "
                                           f"workload's required anti-affinity forbids")
        for key, allowed, others in self.affinity_allowed:
            value = _label(node, key)
            if not node.labels:
                self.need("node labels", node.name)
            elif value not in allowed:
                block("POD_AFFINITY", f"{node.name} is not in a {key} domain hosting {others}, which the "
                                      f"workload's required pod affinity demands")
        for spread in self.spreads:
            if not node.labels:
                self.need("node labels", node.name)
            elif _label(node, spread.key) is None:
                block("TOPOLOGY_SPREAD", f"{node.name} has no {spread.key} label, so the workload's existing "
                                         f"spread constraint cannot place a pod there")


# --- placement -------------------------------------------------------------------------------------

_PLAN_FAILURES = {
    "NODE_CAPACITY": "the usable nodes have no CPU/memory left for another replica",
    "POD_ANTI_AFFINITY": "required anti-affinity allows one replica per domain and the usable domains are taken",
    "TOPOLOGY_SPREAD": "maxSkew would be exceeded: nodes that cannot take a replica (not ready, cordoned, "
                       "tainted, filtered out by capacity or affinity) still count as domains with no pods",
}

# Nodes that nodeTaintsPolicy: Honor stops counting as spread domains: the ones carrying an untolerated taint,
# and the NotReady and cordoned ones, which the control plane taints on its own.
_TAINT_EXCLUSIONS = frozenset({"NODE_TAINT", "NODE_READY", "NODE_SCHEDULABLE"})


def _taint_excluded(state: _NodeState) -> bool:
    return any(blocker.constraint in _TAINT_EXCLUSIONS for blocker in state.blockers)


def _rejection(ctx: _Context, state: _NodeState, counts: Counter, planned: Counter,
               domain: set[str], states: Mapping[str, _NodeState]) -> str | None:
    """Why the next replica cannot go on this node, or None when it can."""
    name = state.node.name
    if planned[name] >= state.cap:
        return "NODE_CAPACITY"
    for key in ctx.self_anti_keys:
        value = _label(state.node, key)
        if value is not None and sum(counts[n] for n, s in states.items() if _label(s.node, key) == value) >= 1:
            return "POD_ANTI_AFFINITY"
    for spread in ctx.spreads:
        totals: Counter = Counter()
        for other, s in states.items():
            value = _label(s.node, spread.key)
            if value is not None and (other in domain or other == name):
                totals[value] += counts[other] + spread.others[other]
        floor = 0 if spread.min_domains and len(totals) < spread.min_domains else min(totals.values())
        if totals[_label(state.node, spread.key)] + 1 - floor > spread.max_skew:
            return "TOPOLOGY_SPREAD"
    if counts[name] + 1 - min(counts[d] for d in domain | {name}) > ctx.max_skew:
        return "TOPOLOGY_SPREAD"
    return None


class _BudgetExhausted(Exception):
    pass


@dataclass
class _Search:
    nodes: list[str] | None  # the node chosen for each replica, in placement order; None when none was found
    reasons: set[str]        # why the deepest dead ends could not take another replica
    exhausted: bool          # the budget ran out before the space was ruled out


def _find(ctx: _Context, states: Sequence[_NodeState], replicas: int, fixed: Sequence[PodModel], *,
          honor_taints: bool, need_spread: bool, budget: int) -> _Search:
    """Depth-first search for a placement of ``replicas`` identical pods, least-loaded node first.

    ``need_spread`` requires the finished placement to use at least two nodes. ``honor_taints`` reads every
    spread constraint as ``nodeTaintsPolicy: Honor``: nodes that cannot take pods for taint reasons are then
    not domains. Unverified facts are assumed favourable, so finding nothing means nothing exists, unless the
    budget ran out, which is reported as such.
    """
    by_name = {s.node.name: s for s in states}
    domain = {s.node.name for s in states if s.domain is True and not (honor_taints and _taint_excluded(s))}
    placeable = [s for s in states if not s.blockers]
    names = [s.node.name for s in placeable]
    fixed_counts = Counter(pod.node for pod in fixed)
    failed: set[tuple[int, ...]] = set()
    deepest: list[Any] = [-1, set()]
    path: list[str] = []
    spent = 0

    def extend(planned: tuple[int, ...]) -> bool:
        nonlocal spent
        if len(path) == replicas:
            return not need_spread or len(set(path)) >= 2
        if planned in failed:
            return False
        spent += 1
        if spent > budget:
            raise _BudgetExhausted
        planned_counts = Counter(dict(zip(names, planned)))
        counts = fixed_counts + planned_counts
        rejected: set[str] = set()
        for i in sorted(range(len(names)), key=lambda i: (counts[names[i]], names[i])):
            reason = _rejection(ctx, placeable[i], counts, planned_counts, domain, by_name)
            if reason is not None:
                rejected.add(reason)
                continue
            path.append(names[i])
            if extend(planned[:i] + (planned[i] + 1,) + planned[i + 1:]):
                return True
            path.pop()
        failed.add(planned)
        if rejected and len(path) >= deepest[0]:
            if len(path) > deepest[0]:
                deepest[0], deepest[1] = len(path), set()
            deepest[1] |= rejected
        return False

    try:
        found = extend(tuple(0 for _ in names))
    except _BudgetExhausted:
        return _Search(None, set(), True)
    return _Search(list(path) if found else None, set() if found else deepest[1], False)


def _assign(movable: Sequence[PodModel], chosen: Sequence[str]) -> dict[str, str]:
    """Give each ready pod a node of the target placement, leaving it where it is whenever it can stay."""
    room = Counter(chosen)
    placement: dict[str, str] = {}
    for pod in movable:
        if room[pod.node] > 0:
            placement[pod.name] = pod.node
            room[pod.node] -= 1
    free = iter(sorted(room.elements()))
    for pod in movable:
        if pod.name not in placement:
            placement[pod.name] = next(free)
    return placement


# --- the public check ------------------------------------------------------------------------------

def check_topology_spread_feasibility(
    deployment: DeploymentGraph,
    service_name: str,
    evidence: SchedulingEvidence | None = None,
    *,
    max_skew: int = DEFAULT_MAX_SKEW,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
) -> FeasibilityResult:
    """Whether spreading the ready replicas of ``service_name`` across worker nodes is schedulable and would
    survive the loss of any one node. The deployment is only read."""

    evidence = evidence if evidence is not None else SchedulingEvidence.from_graph(deployment)
    service = next((item for item in deployment.services if item.name == service_name), None)
    if service is None:
        return FeasibilityResult(
            status=INSUFFICIENT,
            reason=f"Service not found: {service_name}",
            missing_evidence=(f"service {service_name} in the deployment graph",),
        )

    placed = [pod for pod in service.pods if pod.node != UNSCHEDULED_NODE]
    movable = [pod for pod in placed if pod.ready]
    fixed = [pod for pod in placed if not pod.ready]

    if len(movable) < 2:
        return FeasibilityResult(
            status=INFEASIBLE,
            reason=(f"{service_name} has {len(movable)} ready replica(s). A topology spread constraint moves "
                    f"replicas, it does not add them, so losing the node hosting "
                    f"{'its only ready replica' if movable else 'them'} still takes the service down."),
            scheduling=FEASIBLE if movable else None,
            single_node_resilience=False,
            findings=(ConstraintFinding("REPLICA_COUNT", INFEASIBLE,
                                        f"{len(movable)} ready replica(s); at least 2 are needed"),),
        )
    if any(pod.owner_kind == "DaemonSet" for pod in movable):
        return FeasibilityResult(
            status=INFEASIBLE,
            reason=(f"The pods of {service_name} belong to a DaemonSet, which runs one pod per node it "
                    f"selects: a spread constraint does not move them."),
            single_node_resilience=False,
            findings=(ConstraintFinding("WORKLOAD_KIND", INFEASIBLE, "DaemonSet pods are pinned to their nodes"),),
        )

    ctx = _Context(deployment, service, evidence, max_skew, movable)
    resident = Counter(pod.node for pod in movable)
    states = [ctx.assess(node, resident[node.name]) for node in deployment.nodes]
    if ctx.spreads and any(state.domain is False for state in states):
        ctx.need(f"nodeAffinityPolicy of the existing spread constraint of service {service_name} (not captured; "
                 f"Ignore would count the nodes its nodeSelector/affinity filters out as domains with no pods)")
    findings = [blocker for state in states for blocker in state.blockers]
    excluded = any(_taint_excluded(state) for state in states)

    def search(honor_taints: bool, need_spread: bool) -> _Search:
        return _find(ctx, states, len(movable), fixed, honor_taints=honor_taints, need_spread=need_spread,
                     budget=search_budget)

    strict = search(False, True)
    relaxed = search(True, True) if strict.nodes is None and not strict.exhausted and excluded else None

    if strict.exhausted or (relaxed is not None and relaxed.exhausted):
        ctx.need(f"complete placement search: the budget of {search_budget} states ran out before every "
                 f"arrangement of the replicas of {service_name} was ruled out")
        return _inconclusive(service_name, ctx, findings,
                             "No placement was found, but the search stopped before it could rule one out.")
    if relaxed is not None and relaxed.nodes is not None:
        ctx.need("nodeTaintsPolicy: Honor on the spread constraints (Kubernetes >= 1.26): the replicas can only "
                 "be spread if nodes that are NotReady, cordoned or tainted are not counted as domains, and "
                 "the cluster version and the existing constraints' policy are not in the evidence")
        return _inconclusive(service_name, ctx, findings,
                             "A placement exists only if the spread constraint does not count nodes that cannot "
                             "take pods as domains.")

    missing = ctx.missing_evidence()
    if strict.nodes is None:
        # Nothing spreads the replicas over two nodes. Is there any placement at all (scheduling vs resilience)?
        loose = search(excluded, False)
        if loose.nodes is not None:
            findings.append(ConstraintFinding(
                "FAILURE_DOMAINS", INFEASIBLE,
                f"every placement that satisfies the constraints uses {loose.nodes[0]} only, "
                f"so one node failure still takes the replicas"))
            scheduling = INSUFFICIENT if missing else FEASIBLE
            reason = (f"The replicas of {service_name} can be scheduled, but not on two different nodes: fewer "
                      f"than two eligible failure domains, so spreading cannot protect against a node failure.")
        else:
            reasons = strict.reasons | (relaxed.reasons if relaxed is not None else set())
            findings += [ConstraintFinding(code, INFEASIBLE, _PLAN_FAILURES[code]) for code in sorted(reasons)]
            if not reasons:
                findings.append(ConstraintFinding("NO_ELIGIBLE_NODE", INFEASIBLE,
                                                  "no node passes every verified scheduling filter"))
            scheduling = INSUFFICIENT if loose.exhausted else INFEASIBLE
            reason = (f"No placement of the {len(movable)} ready replicas of {service_name} satisfies the "
                      f"verified constraints (maxSkew={max_skew}), even if every unverified fact is favourable.")
        return FeasibilityResult(
            status=INFEASIBLE, reason=reason, scheduling=scheduling, single_node_resilience=False,
            findings=tuple(findings), missing_evidence=missing,
        )

    placement = _assign(movable, strict.nodes)
    recreate = tuple(pod.name for pod in movable if placement[pod.name] != pod.node)
    nodes_used = sorted(set(placement.values()))
    if missing:
        return FeasibilityResult(
            status=INSUFFICIENT,
            reason=(f"A placement of {service_name} across {len(nodes_used)} nodes exists only if the "
                    f"unverified facts listed in missing_evidence hold. It is not confirmed feasible."),
            scheduling=INSUFFICIENT, single_node_resilience=None,
            findings=tuple(findings), missing_evidence=missing,
        )
    moves = (f"Adding the constraint does not move running pods: reaching this placement means recreating "
             f"{len(recreate)} pod(s) (for example a rollout restart), and the scheduler is expected, not "
             f"guaranteed, to place them there." if recreate else "No pod needs to move.")
    return FeasibilityResult(
        status=FEASIBLE,
        reason=(f"Under the modelled constraints the {len(movable)} ready replicas of {service_name} can be "
                f"placed on {len(nodes_used)} nodes ({', '.join(nodes_used)}), so no single node failure takes "
                f"them all. This is a model prediction, not a scheduler guarantee. {moves}"),
        scheduling=FEASIBLE, single_node_resilience=True,
        findings=tuple(findings), placement=placement, pods_to_recreate=recreate,
    )


def _inconclusive(service_name: str, ctx: _Context, findings: list[ConstraintFinding], why: str) -> FeasibilityResult:
    return FeasibilityResult(
        status=INSUFFICIENT,
        reason=f"{why} The feasibility of spreading {service_name} is undecided, so it is not reported feasible.",
        scheduling=INSUFFICIENT, single_node_resilience=None,
        findings=tuple(findings), missing_evidence=ctx.missing_evidence(),
    )
