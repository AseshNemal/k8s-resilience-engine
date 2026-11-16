"""Current, explicitly reviewed workflow definitions for operational predictions.

A saved declaration, a trace candidate and even a historical freeze are not a current
application binding. Reviews are append-only and pin the exact definition, cluster UID,
namespace and connection. Calls observed in traces do not prove business necessity.
"""
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.workflow import WorkflowGraph

POLICY = "operational-workflow/v1"


def _error(message):
    return EvidenceError("WORKFLOW_REVIEW_REQUIRED", message, http_status=409, component="workflow_dependency_modeling")


def current_cluster_uid():
    from app.integrations.kubernetes.validation_gateway import KubernetesTestClusterGateway
    path = next((p for p in os.environ.get("KUBECONFIG", "").split(os.pathsep) if p), "~/.kube/config")
    try:
        uid = KubernetesTestClusterGateway(path, connected_cluster=True).identity().cluster_uid
    except Exception:
        uid = None
    if not uid:
        raise _error("The cluster UID cannot be verified. Retry before using a workflow for operational analysis.")
    return uid


def _source(kind, identity):
    from app.engine.workflow_dependency_modeling import service
    if kind == "frozen":
        frozen = service.get_frozen_workflow_graph(identity)
        if not frozen.trace_ids or frozen.valid_trace_count != len(set(frozen.trace_ids)):
            raise _error("The frozen graph has no reproducible trace evidence.")
        return frozen.graph, frozen.freeze_id, frozen.to_mapping(), frozen
    document = service.read_managed_document(identity)
    if not document.get("reviewed") or document.get("deleted") or not document.get("graph"):
        raise _error("Save a reviewed workflow definition first.")
    return WorkflowGraph.model_validate(document["graph"]), str(document["version"]), document, None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _identity(kind, workflow_id, namespace):
    return _digest([kind, workflow_id, namespace])


def review_workflow(request):
    from app.engine.workflow_dependency_modeling import management
    facts = management._bound(request.namespace, request.connection_fingerprint)
    graph, version, source, _ = _source(request.kind, request.workflow_id)
    if request.source_version != version:
        raise _error("The definition changed since you reviewed it. Reload the exact revision before continuing.")
    if request.kind == "managed" and (source["namespace"] != request.namespace or
            source["connectionFingerprint"] != request.connection_fingerprint):
        raise _error("This definition belongs to another application or connection.")
    referenced = set(graph.mandatory_services) | set(graph.optional_services)
    for edge in graph.edges:
        referenced.update((edge.source, edge.target))
    present = {s["name"] for s in facts["services"]}
    if not referenced or referenced - present or not graph.mandatory_services:
        raise _error("Every workflow service must be discovered in this application before review.")
    uid = current_cluster_uid()
    record = {"policy": POLICY, "kind": request.kind, "workflowId": request.workflow_id,
              "sourceVersion": version, "sourceDigest": _digest(source), "namespace": request.namespace,
              "connectionFingerprint": request.connection_fingerprint, "clusterUid": uid,
              "reviewedBy": request.actor.strip(), "statement": request.statement.strip(),
              "reviewedAt": datetime.now(timezone.utc).isoformat(),
              "basis": "trace-reviewed" if request.kind == "frozen" else "operator-reviewed",
              "observedCallCount": sum(edge.get("interactionBasis") == "observed" for edge in source.get("edges", [])),
              "dependencyNecessity": "assessed-and-approved" if request.kind == "frozen" else "operator-declared",
              "runtimeTraceVerification": "not_performed",
              "observedDependencies": request.kind == "frozen", "isMeasurement": False}
    # Refuse a connection switch during the review rather than binding old facts to a new UID.
    management._bound(request.namespace, request.connection_fingerprint)
    if current_cluster_uid() != uid:
        raise _error("The cluster changed during review. Reload before reviewing again.")
    return management.repository().append("operational-review", _identity(request.kind, request.workflow_id, request.namespace),
                                          request.expected_version, record)


def operational_workflows(namespace, fingerprint=None):
    """Only current reviewed definitions. Missing/stale reviews are never upgraded or replaced."""
    from app.engine.workflow_dependency_modeling import management
    rows = [r for r in management.repository().list_latest("operational-review")
            if r.get("policy") == POLICY and r.get("namespace") == namespace]
    if not rows:
        return []
    fingerprint = fingerprint or management.catalog(namespace)["connectionFingerprint"]
    uid = current_cluster_uid()
    result = []
    for review in rows:
        if review.get("connectionFingerprint") != fingerprint or review.get("clusterUid") != uid:
            continue
        try:
            graph, version, source, frozen = _source(review["kind"], review["workflowId"])
        except (LookupError, EvidenceError):
            continue
        if version != review["sourceVersion"] or _digest(source) != review["sourceDigest"]:
            continue
        if review["kind"] == "managed" and (source["namespace"] != namespace or source["connectionFingerprint"] != fingerprint):
            continue
        result.append(SimpleNamespace(graph=graph, freeze_id=frozen.freeze_id if frozen else None,
            assessment_id=frozen.assessment_id if frozen else None, frozen_at=frozen.frozen_at if frozen else None,
            operational_review=review, display_name=source.get("name")))
    # A trace-backed reviewed definition takes precedence over another reviewed source for the same identity.
    result.sort(key=lambda item: (item.operational_review["kind"] != "frozen", item.graph.workflow_id))
    return list({item.graph.workflow_id: item for item in reversed(result)}.values())


def get_operational_workflow(workflow_id, namespace):
    for item in operational_workflows(namespace):
        if item.graph.workflow_id == workflow_id:
            return item
    raise _error("This workflow has no current review for this cluster and application. Review the exact real definition in Workflow Discovery; workflow survivability is unknown until then.")
