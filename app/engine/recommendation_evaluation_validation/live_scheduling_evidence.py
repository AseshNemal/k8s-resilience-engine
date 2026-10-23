"""The scheduling facts the live feasibility check needs, taken from Member 2 where Member 2 has them, and read independently where nobody does.

Why this exists. The live pipeline sizes a change on the topology Component 3's provider hands it, which is Component 2's
``discover_live``. That graph says where pods run, but leaves the nodes without labels or Ready state and the services without
declared intent, and the DeploymentGraph contract has no taints at all. The feasibility check cannot call a change feasible
without them, so every live candidate stopped at INSUFFICIENT_EVIDENCE.

Where each fact comes from, in this order of preference (the goal: use another member's data when it exists, make an independent
path only when it does not, and change no one else's code):

* node labels, Ready state, schedulability, the CPU and memory still free, and each service's declared intent (selector,
  requests, tolerations, node selector, affinity, spread): Component 2 already produces these in ``analyze_live_topology``, its
  fuller public analysis. They are taken from it, read-only, onto a COPY of the graph Member 4 evaluates;
* node taints: no component supplies them, so Member 4 reads them itself from the nodes, read-only (``node_taints``).

Rules for the merge. Nothing is invented and nothing is loosened. A value the evaluated graph already holds is kept. A fact Component
2 could not provide stays unknown, so the feasibility check says INSUFFICIENT_EVIDENCE for it instead of a guess. Where both graphs know
a node's free CPU or memory, the smaller is used; a figure the evaluated graph does not know is NOT taken from the fuller analysis,
because that analysis reports an unreadable capacity as 0.0. A node either graph calls unschedulable is unschedulable. Pod placement is never
touched: it stays exactly what the completeness-checked discovery observed. If the second read fails, nothing is enriched and the
reason is reported.
"""

from typing import Any

from app.engine.deployment_topology_modeling import discovery
from app.engine.deployment_topology_modeling import service as topology
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import SchedulingEvidence
from app.integrations.kubernetes.client import ClusterAccessError
from app.integrations.kubernetes.node_taints import read_node_taints
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.deployment import DeploymentGraph

_UNAVAILABLE = (EvidenceError, topology.ClusterUnavailableError, ClusterAccessError)


def _smaller(first: float | None, second: float | None) -> float | None:
    known = [v for v in (first, second) if v is not None]
    return min(known) if known else None


def enrich_scheduling_facts(deployment: DeploymentGraph, namespace: str, reader=None) -> tuple[DeploymentGraph, dict[str, Any]]:
    """A copy of ``deployment`` with Component 2's node and intent facts added where it lacks them, and a report of what happened."""
    report: dict[str, Any] = {"source": None, "nodesEnriched": [], "servicesWithIntent": [], "servicesWithoutIntent": [],
                              "unavailable": None}
    try:
        analysis = (reader or topology.analyze_live_topology)(namespace=namespace)
    except _UNAVAILABLE as error:
        report["unavailable"] = getattr(error, "code", type(error).__name__)
        return deployment, report
    report["source"] = getattr(analysis, "data_source", None)
    observed = analysis.graph
    enriched = deployment.model_copy(deep=True)

    nodes = {node.name: node for node in observed.nodes}
    for node in enriched.nodes:
        fuller = nodes.get(node.name)
        if fuller is None:
            continue
        before = (bool(node.labels), node.ready, node.cpu_available, node.memory_available, node.schedulable)
        if not node.labels and fuller.labels:
            node.labels = dict(fuller.labels)
        if node.ready is None:
            node.ready = fuller.ready
        # Capacity is only ever reduced, and only where the evaluated graph already knows it. Component 2's fuller analysis turns a
        # node whose capacity could not be read into 0.0 (discover_live keeps it unknown), so an unknown figure is NOT filled in
        # from it: unknown stays unknown, and the feasibility check says so.
        if node.cpu_available is not None:
            node.cpu_available = _smaller(node.cpu_available, fuller.cpu_available)
        if node.memory_available is not None:
            node.memory_available = _smaller(node.memory_available, fuller.memory_available)
        node.schedulable = node.schedulable and fuller.schedulable
        if before != (bool(node.labels), node.ready, node.cpu_available, node.memory_available, node.schedulable):
            report["nodesEnriched"].append(node.name)

    services = {service.name: service for service in observed.services}
    for service in enriched.services:
        fuller = services.get(service.name)
        if service.declared_intent is None and fuller is not None and fuller.declared_intent is not None:
            service.declared_intent = fuller.declared_intent.model_copy(deep=True)
        (report["servicesWithIntent"] if service.declared_intent is not None else report["servicesWithoutIntent"]).append(service.name)
    return enriched, report


def live_scheduling_evidence(deployment: DeploymentGraph, source=None) -> SchedulingEvidence:
    """What the graph carries, plus the taints read from the cluster's nodes (the graph's own, if any, win)."""
    carried = SchedulingEvidence.from_graph(deployment)
    try:
        read = read_node_taints(source if source is not None else discovery.get_cluster_source())
    except (ClusterAccessError, EvidenceError):
        return carried          # unread stays unread
    return SchedulingEvidence(node_taints={**read, **dict(carried.node_taints)})
