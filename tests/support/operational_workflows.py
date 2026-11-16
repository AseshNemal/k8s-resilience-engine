"""Explicitly reviewed Component 1 doubles for consumer tests (no real cluster/storage).

Policy persistence and rejection of unreviewed data are tested against the real
review service in test_operational_evidence.py, not through this double.
"""
import hashlib
import json
from types import SimpleNamespace


def review_record(graph, namespace="shop", fingerprint="fake-connection", source_version="f1"):
    return {"policy": "operational-workflow/v1", "kind": "frozen", "workflowId": graph.workflow_id,
        "sourceVersion": source_version, "sourceDigest": hashlib.sha256(json.dumps(graph.model_dump(), sort_keys=True).encode()).hexdigest(),
        "namespace": namespace, "connectionFingerprint": fingerprint, "clusterUid": "test-cluster-uid",
        "reviewedBy": "Test application owner", "statement": "The exact real definition was reviewed for this test application.",
        "basis": "trace-reviewed", "observedDependencies": True, "isMeasurement": False}


def install_reviewed_source(monkeypatch, fingerprint="fake-connection"):
    from app.engine.workflow_dependency_modeling import service
    def get(identity, namespace):
        source = service.get_frozen_workflow_graph(identity)
        return SimpleNamespace(graph=source.graph, freeze_id=source.freeze_id, assessment_id=source.assessment_id,
                               frozen_at=getattr(source, "frozen_at", None),
                               operational_review=review_record(source.graph, namespace, fingerprint, source.freeze_id), display_name=None)
    def listed(namespace, connection_fingerprint=None):
        found = []
        for assessment in service.list_dependency_assessments():
            try:
                item = get(assessment.workflow_id, namespace)
            except LookupError:
                continue
            if item.graph.workflow_id not in {f.graph.workflow_id for f in found}:
                found.append(item)
        return found
    monkeypatch.setattr(service, "get_operational_workflow", get)
    monkeypatch.setattr(service, "list_operational_workflows", listed)


def provenance(namespace="shop"):
    """A test-only operational review marker for approval boundary doubles."""
    return {"workflowEvidencePolicy": "operational-workflow/v1", "namespace": namespace,
            "workflowReview": {"policy": "operational-workflow/v1", "sourceDigest": "a" * 64,
                "sourceVersion": "1", "kind": "managed", "connectionFingerprint": "test-connection",
                "clusterUid": "test-cluster", "reviewedBy": "Test owner", "statement": "Verified exact real application definition.",
                "namespace": namespace}}
