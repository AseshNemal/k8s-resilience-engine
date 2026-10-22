"""Declared intent from the cluster: the manifest last applied with kubectl, not a spec changed by hand.

`kubectl scale` changes spec.replicas but leaves the last-applied-configuration annotation alone, so a
Deployment applied with 1 replica and scaled to 3 must still be declared with 1, and show as drift.
"""
import json

from app.engine.deployment_topology_modeling.manifest_parsing.manifest_parser import parse_declared_intents
from app.engine.deployment_topology_modeling.service import LAST_APPLIED_ANNOTATION, _declared_document


def live_deployment(spec_replicas, applied_replicas=None, applied_namespace=None):
    item = {
        "metadata": {"name": "reviews-v1", "namespace": "bookinfo", "annotations": {}},
        "spec": {"replicas": spec_replicas, "selector": {"matchLabels": {"app": "reviews"}},
                 "template": {"metadata": {"labels": {"app": "reviews"}}, "spec": {"containers": [{"name": "r"}]}}},
        "status": {"readyReplicas": spec_replicas},
    }
    if applied_replicas is not None:
        applied = {"apiVersion": "apps/v1", "kind": "Deployment",
                   "metadata": {"name": "reviews-v1", **({"namespace": applied_namespace} if applied_namespace else {})},
                   "spec": {**item["spec"], "replicas": applied_replicas}}
        item["metadata"]["annotations"][LAST_APPLIED_ANNOTATION] = json.dumps(applied)
    return item


def declared_replicas(item):
    (intent,) = parse_declared_intents([_declared_document(item)])
    return intent.expected_replicas, intent.namespace


def test_a_hand_scaled_deployment_is_declared_as_last_applied():
    assert declared_replicas(live_deployment(spec_replicas=3, applied_replicas=1, applied_namespace="bookinfo")) == (1, "bookinfo")


def test_a_manifest_applied_with_dash_n_takes_the_live_namespace():
    assert declared_replicas(live_deployment(spec_replicas=3, applied_replicas=1)) == (1, "bookinfo")


def test_without_the_annotation_the_live_spec_is_the_declared_intent():
    assert declared_replicas(live_deployment(spec_replicas=3)) == (3, "bookinfo")


def test_an_unreadable_annotation_falls_back_to_the_live_spec():
    item = live_deployment(spec_replicas=2)
    item["metadata"]["annotations"][LAST_APPLIED_ANNOTATION] = "{not json"
    assert declared_replicas(item) == (2, "bookinfo")


def test_status_is_never_part_of_the_declared_intent():
    assert "status" not in _declared_document(live_deployment(spec_replicas=3))
