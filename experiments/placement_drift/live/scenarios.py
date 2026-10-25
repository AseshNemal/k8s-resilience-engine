"""The eight Study Guide section 10 scenarios as REAL cluster actions.

Each scenario performs one kubectl action after T0 is observed; the Kubernetes scheduler then
decides placement, and T1 is observed by the Component 2 collector. Where a node or pod must be
chosen, a fixed rule is used (documented on each scenario) so runs are reproducible.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from experiments.placement_drift.constants import DisruptionType
from experiments.placement_drift.live.kubectl import Kubectl
from experiments.placement_drift.live.workloads import STANDIN, Workload

# The stand-in workload's target; kept as names because the stored results and the dashboard use them.
TARGET_SERVICE = STANDIN.target_service
TARGET_REPLICAS = STANDIN.target_replicas
FILLER_LABEL = "experiment=placement-drift-filler"
FILLER_HEADROOM_CORES = 0.03  # leaves less than one 50m target pod of free CPU on a filled node


@dataclass
class RunContext:
    kube: Kubectl
    t0: Any                    # service.TopologyAnalysis observed at T0
    deployments: list[str]
    observe_nodes: Callable[[], list[Any]] = None  # fresh ClusterNode list (used by contention)
    workload: Workload = STANDIN   # which target the scenarios disrupt
    notes: dict[str, Any] = field(default_factory=dict)


def _target_pods(ctx: RunContext) -> list[Any]:
    return next(s.pods for s in ctx.t0.graph.services if s.name == ctx.workload.target_service)


def _busiest_node(pods) -> str:
    """Node hosting the most of the given pods; ties broken by node name."""
    counts = Counter(p.node for p in pods)
    return min(counts, key=lambda n: (-counts[n], n))


@dataclass(frozen=True)
class LiveScenario:
    scenario_id: str
    name: str
    disruption_type: DisruptionType
    description: str
    disrupt: Callable[[RunContext], dict[str, Any]]
    undo: Callable[[RunContext], None] = lambda ctx: None


def _initial(ctx: RunContext) -> dict[str, Any]:
    # Placement right after a fresh deployment is the T0 state itself; nothing is changed.
    return {"action": "none (observe fresh deployment)"}


def _scale_up(ctx):
    target = ctx.workload.target_deployment
    ctx.kube.scale(target, 6)
    return {"action": f"kubectl scale deployment/{target} --replicas=6"}


def _scale_down(ctx):
    target = ctx.workload.target_deployment
    ctx.kube.scale(target, 2)
    return {"action": f"kubectl scale deployment/{target} --replicas=2"}


def _rolling_update(ctx):
    target = ctx.workload.target_deployment
    ctx.kube.rollout_restart(target)
    return {"action": f"kubectl rollout restart deployment/{target}"}


def _pod_deletion(ctx):
    # Delete the target pod that sits on the node with the FEWEST target pods: its replacement
    # decides whether the service stays spread.
    pods = _target_pods(ctx)
    counts = Counter(p.node for p in pods)
    victim = min(pods, key=lambda p: (counts[p.node], p.name))
    ctx.kube.delete_pod(victim.name)
    return {"action": f"kubectl delete pod {victim.name}", "deleted_pod": victim.name, "node": victim.node}


def _cordon(ctx):
    # Cordon the node with the most target pods, then restart the target so new pods must be
    # scheduled while that node is unavailable (cordon alone moves no pod).
    node = _busiest_node(_target_pods(ctx))
    target = ctx.workload.target_deployment
    ctx.notes["cordoned"] = node
    ctx.kube.cordon(node)
    ctx.kube.rollout_restart(target)
    return {"action": f"kubectl cordon {node}; kubectl rollout restart deployment/{target}", "node": node}


def _uncordon_noted(key: str):
    def undo(ctx):
        if key in ctx.notes:
            ctx.kube.uncordon(ctx.notes[key])
    return undo


def _drain(ctx):
    # Drain the node hosting the most experiment pods (all services).
    all_pods = [p for s in ctx.t0.graph.services for p in s.pods]
    node = _busiest_node(all_pods)
    ctx.notes["drained"] = node
    ctx.kube.drain(node)
    return {"action": f"kubectl drain {node} --ignore-daemonsets --delete-emptydir-data", "node": node}


def _filler_manifest(namespace: str, node: str, cpu_m: int) -> str:
    # A pause pod pinned to one node (nodeSelector) that only books CPU. It does no work.
    name = f"capacity-filler-{node}"
    return f"""apiVersion: apps/v1
kind: Deployment
metadata: {{name: {name}, namespace: {namespace}, labels: {{experiment: placement-drift-filler}}}}
spec:
  replicas: 1
  selector: {{matchLabels: {{app: {name}}}}}
  template:
    metadata: {{labels: {{app: {name}, experiment: placement-drift-filler}}}}
    spec:
      nodeSelector: {{kubernetes.io/hostname: {node}}}
      terminationGracePeriodSeconds: 0
      containers:
      - name: filler
        image: registry.k8s.io/pause:3.10
        imagePullPolicy: IfNotPresent
        resources: {{requests: {{cpu: {cpu_m}m}}}}
"""


def _constrained_resources(ctx):
    """Scale the target to 0, fill every worker except one with a CPU 'filler' pod so that less
    than one target pod fits there, then scale the target back to 3. The scheduler can then only
    use the remaining node: does limited capacity cause co-location?"""
    import tempfile

    target = ctx.workload.target_deployment
    # 1. Remove the target's pods, so the free CPU we measure next is without them.
    ctx.kube.scale(target, 0)
    ctx.kube.wait_until_no_pods(ctx.workload.target_selector)
    # 2. Free CPU per worker (allocatable minus what is already requested).
    nodes = ctx.observe_nodes()
    free = {n.name: float(n.cpu_available) for n in nodes}
    # 3. Keep the roomiest node free and fill every other one.
    keep = max(free, key=lambda n: (free[n], n))
    fillers = {}
    for node, cores in free.items():
        if node == keep:
            continue
        # Book all free CPU except 30m, which is less than one target pod needs, so none fits there.
        cpu_m = int((cores - FILLER_HEADROOM_CORES) * 1000)
        if cpu_m <= 0:
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(_filler_manifest(ctx.kube.namespace, node, cpu_m))
        ctx.kube.apply(f.name)
        fillers[node] = cpu_m
    ctx.kube.wait_until_stable([f"capacity-filler-{n}" for n in fillers])
    # 4. Bring the target back to 3: the scheduler can only use the node that was kept free.
    ctx.kube.scale(target, ctx.workload.target_replicas)
    return {"action": "scale target to 0, fill CPU on all workers but one, scale target to 3",
            "kept_free_node": keep, "filler_cpu_millicores": fillers}


def _remove_fillers(ctx):
    ctx.kube.run("delete", "deployment", "-l", FILLER_LABEL, "--ignore-not-found", "--wait=true")


# Each entry: id, name, disruption type, the question it answers, the action, and (if needed) its undo.
SCENARIOS: list[LiveScenario] = [
    LiveScenario("01_initial_deployment", "Initial deployment", DisruptionType.INITIAL_DEPLOYMENT,
                 "Fresh deployment, no disruption (baseline placement and exposure).", _initial),
    LiveScenario("02_scale_up", "Scale up 3 -> 6", DisruptionType.SCALE_UP_STRESS,
                 "Do new replicas stay spread?", _scale_up),
    LiveScenario("03_scale_down", "Scale down 3 -> 2", DisruptionType.SCALE_DOWN_LOSS,
                 "Do the remaining replicas stay spread?", _scale_down),
    LiveScenario("04_rolling_update", "Rolling update", DisruptionType.ROLLING_UPDATE_CHURN,
                 "Where do the replacement pods land?", _rolling_update),
    LiveScenario("05_pod_deletion", "Pod deletion / reschedule", DisruptionType.POD_DELETION_CHAOS,
                 "Where does the replacement pod land?", _pod_deletion),
    LiveScenario("06_node_cordon", "Node cordon + restart", DisruptionType.NODE_CORDON,
                 "New pods cannot use the cordoned node.", _cordon, _uncordon_noted("cordoned")),
    LiveScenario("07_node_drain", "Node drain", DisruptionType.NODE_DRAIN_EVICTION,
                 "Evicted pods move to the remaining nodes.", _drain, _uncordon_noted("drained")),
    LiveScenario("08_constrained_resources", "Constrained resources", DisruptionType.RESOURCE_CONTENTION,
                 "Only one node has free CPU when the target scales back up.",
                 _constrained_resources, _remove_fillers),
]

SCENARIOS_BY_ID = {s.scenario_id: s for s in SCENARIOS}
