"""A capacity ledger for virtual moves: CPU and memory as pods change nodes in a DeploymentGraph copy.

Why it exists. Phase 1 judges one change against the capacity the graph reports. Once a first change has been
applied to a virtual graph its pods sit on new nodes, but the nodes still report the old remaining capacity, so
a second change would be checked against room that is already taken (or that has just been freed). The ledger
keeps one account per node and moves a pod's request from one account to the other, so every change after the
first sees the capacity the earlier ones left.

What the numbers mean. ``cpu_available`` (cores) and ``memory_available`` (GiB) are what is left on a node for
a new pod. A move debits the destination by the pod's request and credits the origin by the same amount.
Capacity is therefore conserved: it is never created or destroyed, and a pod's request is credited to its
origin once and debited from its destination once. A pod can be moved at most once per ledger, so its capacity
cannot be freed or reserved twice, and a node's room that one change has used cannot be used by the next.

Final state only. The ledger describes the FINAL state of a set of moves. Each move must fit at the moment it
is made, which is a conservative replay: two full nodes cannot swap pods, because neither move fits first.
It says nothing about rollout time (a surge pod scheduled before the old one is gone, the order two Deployments
update in); that depends on the Deployment's update strategy, which is not in the evidence, and is reported as
not evaluated by the code that uses the ledger.

Unknowns stay unknown. A node whose capacity is unknown stays unknown and refuses any move that needs it. A pod
whose request is unknown (no intent, no containers captured, an unparseable quantity) makes both nodes it
touches unknown rather than being guessed. Only CPU and memory are tracked: extended resources, ephemeral
storage, pod counts and the like are not in the evidence.

The ledger reads the graph it is built from and never changes it. ``write_back`` writes the remaining capacity
into a graph the caller supplies, which is always a working copy.
"""

from collections.abc import Mapping

from app.engine.recommendation_evaluation_validation.scheduling_constraints import pod_requests
from app.engine.recommendation_evaluation_validation.simulator import simulate_topology_spread
from app.shared.schemas.deployment import DeploymentGraph, ServicePlacement

EPSILON = 1e-9
_RESOURCES = ("cpu", "memory")


class LedgerError(Exception):
    """Base class: a move the ledger cannot account for."""


class UnknownPod(LedgerError):
    def __init__(self, pod: str) -> None:
        super().__init__(f"pod {pod} is not in the graph")
        self.pod = pod


class AlreadyMoved(LedgerError):
    def __init__(self, pod: str) -> None:
        super().__init__(f"pod {pod} was already moved; its capacity can be freed and reserved only once")
        self.pod = pod


class UnknownCapacity(LedgerError):
    def __init__(self, node: str, reason: str) -> None:
        super().__init__(f"the {reason} of node {node} is unknown, so a move that needs it cannot be accounted for")
        self.node, self.reason = node, reason


class CapacityExceeded(LedgerError):
    def __init__(self, node: str, resource: str, needed: float, available: float) -> None:
        super().__init__(f"node {node} has {available:g} {resource} left, less than the {needed:g} the pod asks for")
        self.node, self.resource, self.needed, self.available = node, resource, needed, available


def _request_of(service: ServicePlacement) -> tuple[float, float] | None:
    """(CPU cores, memory GiB) one pod of the service asks for, or None when that is not known."""
    intent = service.declared_intent
    if intent is None or len(service.deployment_names) > 1 or not intent.resource_requests:
        return None
    try:
        return pod_requests(intent.resource_requests)
    except (ValueError, KeyError, TypeError):
        return None


class CapacityLedger:
    """One account of remaining CPU and memory per node, for the FINAL state of a set of virtual moves.

    See the module docstring: capacity is conserved, a pod moves at most once, unknowns stay unknown, and
    nothing about rollout time is modelled.
    """

    def __init__(self, deployment: DeploymentGraph) -> None:
        self._available: dict[str, list[float | None]] = {
            node.name: [node.cpu_available, node.memory_available] for node in deployment.nodes}
        self._initial = {name: tuple(values) for name, values in self._available.items()}
        self._requests = {service.name: _request_of(service) for service in deployment.services}
        self._pods = {pod.name: (service.name, pod.node) for service in deployment.services for pod in service.pods}
        self._moved: dict[str, tuple[str, str]] = {}

    @property
    def moved_pods(self) -> dict[str, tuple[str, str]]:
        """pod -> (node it left, node it went to), for every pod that really changed node."""
        return dict(self._moved)

    def available(self, node: str) -> tuple[float | None, float | None]:
        if node not in self._available:
            raise UnknownCapacity(node, "capacity")
        cpu, memory = self._available[node]
        return cpu, memory

    def net_reserved(self, node: str) -> tuple[float | None, float | None]:
        """How much more of each resource is taken on the node than at the start (negative: freed)."""
        current, initial = self.available(node), self._initial[node]
        return tuple(None if now is None or then is None else then - now for now, then in zip(current, initial))

    def move(self, pod_name: str, to_node: str) -> None:
        """Account for moving one pod; refuse, leaving the ledger unchanged, if it cannot be accounted for."""
        if pod_name not in self._pods:
            raise UnknownPod(pod_name)
        service, origin = self._pods[pod_name]
        if pod_name in self._moved:
            raise AlreadyMoved(pod_name)
        if to_node == origin:
            return
        if to_node not in self._available:
            raise UnknownCapacity(to_node, "existence in the graph")

        request = self._requests[service]
        if request is None:
            self._forget(origin)
            self._forget(to_node)
        else:
            self._check_fits(to_node, request)       # nothing changes unless the whole move fits
            self._debit(to_node, request)
            self._credit(origin, request)
        self._moved[pod_name] = (origin, to_node)

    def write_back(self, deployment: DeploymentGraph) -> None:
        """Put the remaining capacity into ``deployment``, which must be a working copy."""
        for node in deployment.nodes:
            if node.name in self._available:
                node.cpu_available, node.memory_available = self._available[node.name]

    # -- the two accounts of one move ----------------------------------------------------------------

    def _check_fits(self, node: str, request: tuple[float, float]) -> None:
        for resource, need, have in zip(_RESOURCES, request, self._available[node]):
            if need <= EPSILON:
                continue
            if have is None:
                raise UnknownCapacity(node, resource)
            if have + EPSILON < need:
                raise CapacityExceeded(node, resource, need, have)

    def _debit(self, node: str, request: tuple[float, float]) -> None:
        account = self._available[node]
        for index, need in enumerate(request):
            if need > EPSILON and account[index] is not None:
                account[index] = max(0.0, account[index] - need)

    def _credit(self, node: str, request: tuple[float, float]) -> None:
        account = self._available.get(node)         # a pod outside the tracked nodes frees nothing we track
        if account is None:
            return
        for index, amount in enumerate(request):
            if account[index] is not None:
                account[index] += amount

    def _forget(self, node: str) -> None:
        if node in self._available:
            self._available[node] = [None, None]


class LedgerPlacementMutation:
    """A DeploymentMutation: move a service's ready pods to a verified placement, capacity included.

    Unlike ``TopologySpreadMutation`` it keeps the nodes' remaining capacity in step with the pods, so the next
    change applied to the result is checked against what this one left. Raises ``LedgerError`` if the placement
    does not fit; the engine then discards the working copy, so no partial change is ever visible.
    """

    def __init__(self, service_name: str, placement: Mapping[str, str]) -> None:
        self._service_name = service_name
        self._placement = dict(placement)

    def apply(self, deployment: DeploymentGraph) -> DeploymentGraph:
        service = next((s for s in deployment.services if s.name == self._service_name), None)
        if service is None:
            raise ValueError(f"Service not found: {self._service_name}")

        ledger = CapacityLedger(deployment)
        pending = sorted(pod.name for pod in service.pods
                         if pod.ready and self._placement.get(pod.name, pod.node) != pod.node)
        while pending:
            waiting: list[str] = []
            first_refusal: CapacityExceeded | None = None
            for name in pending:
                try:
                    ledger.move(name, self._placement[name])
                except CapacityExceeded as refusal:      # another move in this change may free the room
                    waiting.append(name)
                    first_refusal = first_refusal or refusal
            if len(waiting) == len(pending):
                raise first_refusal
            pending = waiting

        simulate_topology_spread(deployment, self._service_name, self._placement)
        ledger.write_back(deployment)
        return deployment
