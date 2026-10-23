"""Offline evaluation harness for Member 4 (recommendation, evaluation and validation).

TEST-ONLY. It lets Member 4's pipeline be developed and checked while Component 1 has no frozen workflow for
the connected application. Everything here is a controlled, deterministic artifact written by hand and labelled
as such; none of it is, or may be shown as, evidence read from a cluster.

How it stays out of the product:
* it lives under ``tests/`` and ``app`` never imports it (``tests/test_member4_harness_isolation.py`` checks);
* every evaluation it runs carries ``OFFLINE_SOURCE`` as its evidence source, never ``LIVE_EVIDENCE_SOURCE``.
  The approval gate trusts only the live source (``has_verified_provenance``), so a recommendation computed here
  can be inspected and ranked but never approved;
* it calls Component 3 only through ``predict_failure_impact`` (the public interface) and reuses Member 4's
  own ``evaluate_recommendation`` core. It re-implements neither.

The benchmark is Bookinfo-shaped (productpage -> details and reviews -> ratings) but multi-replica, because the
real Bookinfo runs every service as one replica and a spread change cannot help a single replica. It is a model
of that application's shape, not a measurement of it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.engine.recommendation_evaluation_validation.approval import LiveRecommendation
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import SchedulingEvidence
from app.engine.recommendation_evaluation_validation.live_pipeline import (
    EvidenceProvenance,
    evaluate_recommendation,
)
from app.shared.schemas.deployment import (
    ClusterMetadata,
    DeclaredPlacementIntent,
    DeploymentGraph,
    NodeModel,
    PodModel,
    ServicePlacement,
)
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph

# Never equal to LIVE_EVIDENCE_SOURCE: that difference is what makes harness output unapprovable.
OFFLINE_SOURCE = "offline-test-fixture"
OFFLINE_NAMESPACE = "offline-benchmark"
DEFAULT_REQUESTS = {"cpu": "100m", "memory": "128Mi"}
HOSTNAME = "kubernetes.io/hostname"


def make_node(name: str, *, zone: str | None = "zone-a", ready: bool | None = True, schedulable: bool = True,
              cpu: float | None = 4.0, memory: float | None = 8.0, labels: Mapping[str, str] | None = None) -> NodeModel:
    """A worker with its hostname label, like a real one; pass ``labels={}`` to model unread labels."""
    return NodeModel(name=name, zone=zone, cpu_available=cpu, memory_available=memory, ready=ready,
                     schedulable=schedulable, labels={HOSTNAME: name} if labels is None else dict(labels))


def make_service(
    name: str, nodes: Sequence[str], *, declared: int | None = None, ready: Sequence[bool] | None = None,
    intent: DeclaredPlacementIntent | None | bool = True, requests: Sequence[Mapping[str, str]] | None = None,
    node_selector: Mapping[str, str] | None = None, tolerations: Sequence[Mapping[str, object]] = (),
    topology_spread: Sequence = (), pod_anti_affinity_rules: Mapping[str, object] | None = None,
) -> ServicePlacement:
    """One service whose i-th replica runs on ``nodes[i]``. ``intent=False`` leaves the intent unread (None)."""
    pods = [PodModel(name=f"{name}-{i}", node=node, ready=True if ready is None else ready[i], phase="Running",
                     restart_count=0, namespace=OFFLINE_NAMESPACE, owner_kind="ReplicaSet",
                     owner_name=f"{name}-rs") for i, node in enumerate(nodes)]
    count = len(pods) if declared is None else declared
    if intent is True:
        rules = dict(pod_anti_affinity_rules or {})
        intent = DeclaredPlacementIntent(
            declared_replicas=count, selector={"app": name},
            resource_requests=[dict(r) for r in (requests if requests is not None else [DEFAULT_REQUESTS])],
            node_selector=dict(node_selector or {}), tolerations=[dict(t) for t in tolerations],
            topology_spread_constraints=list(topology_spread),
            pod_anti_affinity_configured=bool(rules), pod_anti_affinity_rules=rules)
    ready_count = sum(1 for pod in pods if pod.ready)
    hosts = sorted({pod.node for pod in pods})
    concentration = (max(sum(1 for p in pods if p.ready and p.node == n) for n in hosts) / ready_count
                     if ready_count else None)
    return ServicePlacement(
        name=name, namespace=OFFLINE_NAMESPACE, declared_replicas=count, ready_replicas=ready_count,
        replica_concentration=concentration, concentrated=(len({p.node for p in pods if p.ready}) == 1
                                                           if ready_count else None),
        pods=pods, hosting_nodes=hosts, exposed_failure_domains=hosts,
        declared_intent=intent or None, deployment_names=[name])


def make_deployment(services: Sequence[ServicePlacement], nodes: Sequence[NodeModel], *,
                    snapshot_id: str = "offline-snapshot") -> DeploymentGraph:
    """A topology and the scheduling-evidence-complete nodes it runs on, deep-copied by the callers that need it."""
    return DeploymentGraph(
        snapshot_id=snapshot_id, timestamp="offline", cluster=ClusterMetadata(
            failure_domain_type=HOSTNAME, node_names=[n.name for n in nodes]),
        nodes=list(nodes), services=list(services))


def make_workflow(workflow_id: str, mandatory: Sequence[str], edges: Sequence[tuple[str, str]],
                  optional: Sequence[str] = (), criticality: float = 0.9) -> WorkflowGraph:
    return WorkflowGraph(workflow_id=workflow_id, criticality=criticality, mandatory_services=list(mandatory),
                         optional_services=list(optional),
                         edges=[WorkflowEdge(source=s, target=t) for s, t in edges])


def no_taints(deployment: DeploymentGraph) -> SchedulingEvidence:
    """The scheduling evidence for a cluster that was observed to have no taints at all."""
    return SchedulingEvidence(node_taints={node.name: [] for node in deployment.nodes})


@dataclass
class Scenario:
    """One controlled case: the artifacts to evaluate, and what it is for."""

    name: str
    description: str
    workflow: WorkflowGraph
    deployment: DeploymentGraph
    evidence: SchedulingEvidence
    failed_node: str | None = None
    extra_workflows: list[WorkflowGraph] = field(default_factory=list)

    def evaluate(self, **kwargs) -> LiveRecommendation:
        """Evaluate the scenario; any keyword (``evidence``, ``failed_node``, ...) overrides the scenario's own."""
        options = {"failed_node": self.failed_node, "evidence": self.evidence, **kwargs}
        return evaluate_offline(self.workflow, self.deployment, **options)


def evaluate_offline(workflow: WorkflowGraph, deployment: DeploymentGraph, *, failed_node: str | None = None,
                     evidence: SchedulingEvidence | None = None, **kwargs) -> LiveRecommendation:
    """Member 4's whole evaluation over the given artifacts, labelled as offline.

    ``evidence`` defaults to "no taints anywhere" because a hand-built graph has no taint source; pass a
    ``SchedulingEvidence`` to model taints, or an empty one (``SchedulingEvidence()``) to model taints unread.
    """
    return evaluate_recommendation(
        workflow, deployment,
        EvidenceProvenance(OFFLINE_SOURCE, OFFLINE_NAMESPACE, None, "OFFLINE_FIXTURE", None),
        failed_node, scheduling_evidence=no_taints(deployment) if evidence is None else evidence, **kwargs)


# --- the Bookinfo-shaped benchmark ---------------------------------------------------------------------

BOOKINFO_NODES = ("w1", "w2", "w3")

# productpage needs details and reviews; reviews uses ratings, which is optional (the page renders without stars).
PRODUCT_PAGE_VIEW = ("productpage-view", ("productpage", "details", "reviews"),
                     (("productpage", "details"), ("productpage", "reviews"), ("reviews", "ratings")), ("ratings",))
# A second workflow in which ratings is mandatory, so one service is optional in one and required in another.
RATED_VIEW = ("rated-view", ("productpage", "reviews", "ratings"),
              (("productpage", "reviews"), ("reviews", "ratings")), ())


def bookinfo_workflow(which: tuple = PRODUCT_PAGE_VIEW) -> WorkflowGraph:
    workflow_id, mandatory, edges, optional = which
    return make_workflow(workflow_id, mandatory, edges, optional)


def bookinfo_scenario(layout: str = "concentrated", *, failed_node: str | None = "w1") -> Scenario:
    """Bookinfo's four services with several replicas each.

    ``concentrated``: reviews (3) and ratings (2) sit entirely on w1, the failure that takes the workflow down;
    productpage and details are already spread. ``distributed``: every service is spread, nothing to fix.
    """
    layouts = {
        "concentrated": {"productpage": ("w1", "w2", "w3"), "details": ("w2", "w3"),
                         "reviews": ("w1", "w1", "w1"), "ratings": ("w1", "w1")},
        "distributed": {"productpage": ("w1", "w2", "w3"), "details": ("w2", "w3"),
                        "reviews": ("w1", "w2", "w3"), "ratings": ("w2", "w3")},
    }
    placement = layouts[layout]
    nodes = [make_node(name, zone=f"zone-{name}") for name in BOOKINFO_NODES]
    deployment = make_deployment([make_service(svc, on) for svc, on in placement.items()], nodes)
    return Scenario(
        name=f"bookinfo-{layout}", workflow=bookinfo_workflow(), deployment=deployment,
        evidence=no_taints(deployment), failed_node=failed_node,
        extra_workflows=[bookinfo_workflow(RATED_VIEW)],
        description=f"Offline Bookinfo-shaped benchmark, {layout} layout (hand-written, not read from a cluster).")
