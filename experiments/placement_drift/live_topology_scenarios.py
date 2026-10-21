"""
The eight disruption scenarios, simulated on the connected cluster's real topology.

The scripted scenarios (scenario_01 ... scenario_08) carry their own made-up nodes and pods (worker-1..3,
paymentservice, us-central1 zones). This module runs the same eight disruption types against the nodes and pods
of a real DeploymentGraph instead, so a result describes the cluster that is actually connected.

What it is, and what it is not:
* It changes nothing in the cluster. The disruption is computed on a deep copy of the graph's placements.
* The "after" state (T1) comes from a deliberately simple, deterministic rule, not from the Kubernetes
  scheduler: a pod that has to move goes to the worker with the fewest pods, ties broken by node name. It is a
  prediction of how placement could drift, not an observation of what the scheduler did, so the result stays
  a DRY_RUN_SIMULATION and is never labelled experimental evidence.
* Nothing is invented. The target service and node are chosen from the real graph by fixed rules, and a
  scenario that needs a fact the cluster did not report (CPU and memory for resource contention) stops with an
  explicit error instead of assuming a number.

Target choice (the same every run, so results are reproducible):
* service: the one with the highest replica concentration, then the most pods, then the lowest name;
* node: the worker that hosts the most pods of that service, then the lowest name.
"""
from typing import Any, Callable, Dict, List, Optional

from app.shared.schemas.deployment import DeploymentGraph
from experiments.placement_drift.base_scenario import BaseScenario
from experiments.placement_drift.constants import DisruptionType, ScenarioExecutionMode
from experiments.placement_drift.exceptions import ScenarioExecutionError
from experiments.placement_drift.scenario_models import DisruptionEvent, PodPlacementRecord
from experiments.placement_drift.snapshot_repository import SnapshotRepository

# The graph builder gives pods that the scheduler has not placed this node name; they have no node to model.
UNSCHEDULED_NODE = "unscheduled"

PLACEMENT_RULE = "a pod that has to move goes to the worker with the fewest pods (ties by node name)"


def choose_target_service(graph: DeploymentGraph) -> str:
    """The service the scenarios disturb: most concentrated, then most pods, then lowest name."""
    candidates = [s for s in graph.services if any(p.node != UNSCHEDULED_NODE for p in s.pods)]
    if not candidates:
        raise ScenarioExecutionError(
            "The connected cluster has no service with a placed pod, so there is nothing to disrupt."
        )
    best = max(candidates, key=lambda s: (s.replica_concentration or 0.0, len(s.pods), _inverse(s.name)))
    return best.name


def _inverse(text: str) -> tuple:
    """Sort key that makes max() prefer the lowest name."""
    return tuple(-ord(ch) for ch in text)


class LiveTopologyScenario(BaseScenario):
    """One disruption type, simulated on a real topology. Built from a scripted scenario's identity."""

    topology_source = "live"

    def __init__(
        self,
        template: BaseScenario,
        graph: DeploymentGraph,
        namespace: str,
        execution_mode: ScenarioExecutionMode = ScenarioExecutionMode.DRY_RUN_SIMULATION,
        repository: Optional[SnapshotRepository] = None,
    ):
        # The graph is copied so no scenario can ever change what the caller holds.
        self._graph = graph.model_copy(deep=True)
        super().__init__(
            scenario_id=template.scenario_id,
            scenario_name=f"{template.scenario_name} (live topology)",
            disruption_type=template.disruption_type,
            target_service=choose_target_service(self._graph),
            namespace=namespace,
            execution_mode=execution_mode,
            repository=repository,
        )
        self._cordoned: set[str] = set()

    # -- baseline ------------------------------------------------------------------------------------

    def setup_workload(self) -> None:
        """T0 is exactly the real graph: its worker nodes and the pods placed on them."""
        self._cordoned = set()
        self._cluster_nodes = [
            {"name": n.name, "zone": n.zone, "cpu": n.cpu_available, "memory": n.memory_available}
            for n in self._graph.nodes
        ]
        self._active_pods = [
            PodPlacementRecord(
                pod_name=pod.name, service_name=service.name, node_name=pod.node, zone=pod.zone, is_ready=pod.ready,
            )
            for service in self._graph.services
            for pod in service.pods
            if pod.node != UNSCHEDULED_NODE
        ]

    # -- placement helpers ---------------------------------------------------------------------------

    def _load(self) -> Dict[str, int]:
        load = {n["name"]: 0 for n in self._cluster_nodes}
        for pod in self._active_pods:
            if pod.node_name in load:
                load[pod.node_name] += 1
        return load

    def _eligible(self, excluding: set[str] = frozenset()) -> List[str]:
        return [n["name"] for n in self._cluster_nodes if n["name"] not in self._cordoned and n["name"] not in excluding]

    def _least_loaded(self, excluding: set[str] = frozenset()) -> str:
        eligible = self._eligible(excluding)
        if not eligible:
            raise ScenarioExecutionError("No worker node is left to schedule a pod on.")
        load = self._load()
        return min(eligible, key=lambda name: (load[name], name))

    def _zone_of(self, node_name: str) -> Optional[str]:
        return next((n["zone"] for n in self._cluster_nodes if n["name"] == node_name), None)

    def _record(self, name: str, service: str, node: str, ready: bool = True) -> PodPlacementRecord:
        return PodPlacementRecord(
            pod_name=name, service_name=service, node_name=node, zone=self._zone_of(node), is_ready=ready,
        )

    def _target_pods(self) -> List[PodPlacementRecord]:
        return sorted((p for p in self._active_pods if p.service_name == self.target_service), key=lambda p: p.pod_name)

    def _target_node(self) -> str:
        """The worker hosting the most pods of the target service (ties: lowest name)."""
        counts: Dict[str, int] = {}
        for pod in self._target_pods():
            counts[pod.node_name] = counts.get(pod.node_name, 0) + 1
        return min(counts, key=lambda node: (-counts[node], node))

    def _remove(self, pod_name: str) -> None:
        self._active_pods = [p for p in self._active_pods if p.pod_name != pod_name]

    def _event(self, slug: str, target: str, **parameters: Any) -> DisruptionEvent:
        return DisruptionEvent(
            event_id=f"evt-{self.scenario_id}-live-{slug}",
            disruption_type=self.disruption_type,
            target_resource=target,
            parameters={"simulated": True, "topology_source": "live", "placement_rule": PLACEMENT_RULE,
                        "target_service": self.target_service, **parameters},
        )

    # -- the eight disruptions -----------------------------------------------------------------------

    def execute_disruption(self) -> DisruptionEvent:
        handlers: Dict[DisruptionType, Callable[[], DisruptionEvent]] = {
            DisruptionType.INITIAL_DEPLOYMENT: self._initial_deployment,
            DisruptionType.SCALE_UP_STRESS: self._scale_up,
            DisruptionType.SCALE_DOWN_LOSS: self._scale_down,
            DisruptionType.ROLLING_UPDATE_CHURN: self._rolling_update,
            DisruptionType.POD_DELETION_CHAOS: self._pod_deletion,
            DisruptionType.NODE_CORDON: self._node_cordon,
            DisruptionType.NODE_DRAIN_EVICTION: self._node_drain,
            DisruptionType.RESOURCE_CONTENTION: self._resource_contention,
        }
        return handlers[self.disruption_type]()

    def _initial_deployment(self) -> DisruptionEvent:
        """The baseline itself: nothing moves, so T1 equals T0 and the drift is zero."""
        return self._event("baseline", self.target_service, action="observe the current placement")

    def _scale_up(self) -> DisruptionEvent:
        node = self._least_loaded()
        name = f"{self.target_service}-sim-{len(self._target_pods()) + 1}"
        self._active_pods.append(self._record(name, self.target_service, node))
        return self._event("scale-up", self.target_service, action="add one replica", new_pod=name, placed_on=node)

    def _scale_down(self) -> DisruptionEvent:
        node = self._target_node()
        victim = next(p for p in self._target_pods() if p.node_name == node)
        self._remove(victim.pod_name)
        return self._event("scale-down", self.target_service, action="remove one replica",
                           removed_pod=victim.pod_name, removed_from=node)

    def _rolling_update(self) -> DisruptionEvent:
        moves: Dict[str, str] = {}
        for old in self._target_pods():
            self._remove(old.pod_name)
            node = self._least_loaded()
            new_name = f"{old.pod_name}-updated"
            self._active_pods.append(self._record(new_name, self.target_service, node))
            moves[old.pod_name] = f"{old.node_name} -> {node} as {new_name}"
        return self._event("rolling-update", self.target_service, action="replace every replica one by one",
                           replacements=moves)

    def _pod_deletion(self) -> DisruptionEvent:
        node = self._target_node()
        victim = next(p for p in self._target_pods() if p.node_name == node)
        self._remove(victim.pod_name)
        landed = self._least_loaded()
        replacement = f"{victim.pod_name}-replacement"
        self._active_pods.append(self._record(replacement, self.target_service, landed))
        return self._event("pod-deletion", victim.pod_name, action="delete one pod; its controller replaces it",
                           deleted_from=node, replacement=replacement, replacement_on=landed)

    def _node_cordon(self) -> DisruptionEvent:
        node = self._target_node()
        self._cordoned.add(node)
        # A cordon alone moves nothing; the effect shows when a pod on the node is replaced afterwards.
        victim = next(p for p in self._target_pods() if p.node_name == node)
        self._remove(victim.pod_name)
        landed = self._least_loaded()
        replacement = f"{victim.pod_name}-replacement"
        self._active_pods.append(self._record(replacement, self.target_service, landed))
        return self._event("cordon", node, action="cordon the node, then replace one of its pods",
                           cordoned_node=node, replaced_pod=victim.pod_name, replacement_on=landed)

    def _node_drain(self) -> DisruptionEvent:
        node = self._target_node()
        self._cordoned.add(node)
        moved: Dict[str, str] = {}
        for pod in sorted((p for p in self._active_pods if p.node_name == node), key=lambda p: p.pod_name):
            self._remove(pod.pod_name)
            landed = self._least_loaded()
            self._active_pods.append(self._record(pod.pod_name, pod.service_name, landed, pod.is_ready))
            moved[pod.pod_name] = f"{node} -> {landed}"
        return self._event("drain", node, action="drain the node and evacuate its pods",
                           drained_node=node, migration_paths=moved)

    def _resource_contention(self) -> DisruptionEvent:
        """Every worker but the one with the most free CPU runs out of room, so the target service's pods
        on the others are rescheduled there. Needs the free CPU of every worker; unknown means stop."""
        free: Dict[str, float] = {}
        for node in self._cluster_nodes:
            if node["cpu"] is None:
                raise ScenarioExecutionError(
                    f"Node '{node['name']}' did not report its available CPU, so resource contention cannot "
                    "be simulated. Nothing was assumed."
                )
            free[node["name"]] = float(node["cpu"])
        survivor = min(free, key=lambda name: (-free[name], name))
        exhausted = sorted(set(free) - {survivor})
        self._cordoned.update(exhausted)
        moved: Dict[str, str] = {}
        for pod in self._target_pods():
            if pod.node_name in exhausted:
                self._remove(pod.pod_name)
                self._active_pods.append(self._record(pod.pod_name, pod.service_name, survivor, pod.is_ready))
                moved[pod.pod_name] = f"{pod.node_name} -> {survivor}"
        return self._event("contention", ",".join(exhausted) or survivor,
                           action="exhaust every worker but the one with the most free CPU",
                           exhausted_nodes=exhausted, remaining_node=survivor, migration_paths=moved)

    def rollback(self) -> bool:
        """Nothing was ever changed in the cluster; restoring the baseline just rebuilds it from the graph."""
        self.setup_workload()
        return True
