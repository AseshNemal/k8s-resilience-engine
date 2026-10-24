"""Member 4's fallback for the declared placement intent of a service on a TARGET cluster other than the connected one.

Why it exists. Component 2 attaches each service's declared intent (selector, requests, tolerations, affinity, spread) only when it reads the
cluster the backend is connected to. On a disposable test cluster the topology comes from Component 2's ``discover`` over the target's own
API (the gateway passes its source), which reports where pods run but carries no intent, so every candidate would stop at
INSUFFICIENT_EVIDENCE. Until Component 2 offers a source-taking variant of its fuller analysis, this adapter fills the gap WITHOUT changing
anyone's code: it reads the target's own Deployments, parses them with Component 2's PUBLIC ``parse_declared_intents``, and builds the same
``DeclaredPlacementIntent`` contract the graph carries.

It is origin-tagged ("member4-fallback") in the report it returns, and swapping it for Component 2's own is one call. Nothing is guessed:
a service backed by zero or by several Deployments gets no intent (the pod template is then not one thing), which leaves its candidates
honestly INSUFFICIENT_EVIDENCE, never feasible.
"""

from types import SimpleNamespace
from typing import Any

from app.engine.deployment_topology_modeling.manifest_parsing.manifest_parser import parse_declared_intents
from app.shared.schemas.deployment import DeclaredPlacementIntent, DeploymentGraph, TopologySpreadConstraintModel

ORIGIN = "member4-fallback"
SOURCE_LABEL = "validation-target"


def _document(item: dict[str, Any]) -> dict[str, Any]:
    """The Deployment as a manifest: a live list item has no kind and carries status, which is observed state, not intent."""
    return {"apiVersion": "apps/v1", "kind": "Deployment", **{k: v for k, v in item.items() if k != "status"}}


def _model(intent: dict[str, Any]) -> DeclaredPlacementIntent:
    return DeclaredPlacementIntent(
        declared_replicas=intent.get("expected_replicas", 1), selector=intent.get("selector", {}),
        readiness_probe_configured=intent.get("readiness_probe_configured", False), node_affinity_configured=intent.get("has_node_affinity", False),
        pod_anti_affinity_configured=intent.get("pod_anti_affinity_configured", False), pod_affinity_configured=intent.get("pod_affinity_configured", False),
        node_selector=intent.get("node_selector", {}), tolerations=intent.get("tolerations", []),
        selector_match_expressions=intent.get("selector_match_expressions", []), node_affinity_rules=intent.get("node_affinity_rules", {}),
        pod_affinity_rules=intent.get("pod_affinity_rules", {}), pod_anti_affinity_rules=intent.get("pod_anti_affinity_rules", {}),
        topology_spread_constraints=[TopologySpreadConstraintModel.model_validate(c) for c in intent.get("spread_constraints", [])],
        resource_requests=intent.get("resource_requests", []), resource_limits=intent.get("resource_limits_by_container", []))


def attach_declared_intent(graph: DeploymentGraph, deployments: list[dict[str, Any]], nodes: list[dict[str, Any]] | None = None
                           ) -> tuple[DeploymentGraph, dict[str, Any]]:
    """A copy of ``graph`` whose services carry declared intent read from ``deployments`` and whose nodes carry their labels.

    Returns (graph, report). Pod placement is never touched.
    """
    copy = graph.model_copy(deep=True)
    parsed = {intent.service_name: intent for intent in parse_declared_intents([_document(d) for d in deployments])}
    report: dict[str, Any] = {"origin": ORIGIN, "servicesWithIntent": [], "servicesWithoutIntent": [], "nodeLabelsRead": []}
    for service in copy.services:
        names = [n for n in service.deployment_names if n in parsed]
        if len(names) == 1 and len(service.deployment_names) == 1 and service.declared_intent is None:
            service.declared_intent = _model(parsed[names[0]].model_dump())
        (report["servicesWithIntent"] if service.declared_intent is not None else report["servicesWithoutIntent"]).append(service.name)
    labels = {(n.get("metadata") or {}).get("name"): ((n.get("metadata") or {}).get("labels") or {}) for n in (nodes or [])}
    for node in copy.nodes:
        if not node.labels and labels.get(node.name):
            node.labels = dict(labels[node.name])
            report["nodeLabelsRead"].append(node.name)
    return copy, report


def as_analysis(graph: DeploymentGraph) -> SimpleNamespace:
    """The shape ``enrich_scheduling_facts`` reads from Component 2's fuller analysis (``.graph`` and ``.data_source``)."""
    return SimpleNamespace(graph=graph, data_source=f"{SOURCE_LABEL} ({ORIGIN})")
