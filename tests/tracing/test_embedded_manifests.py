"""The manifests embedded in the host helper (extension/helper/internal/traceinstall) are the reviewed ones.

The helper carries its own copies so the extension works without the Bookinfo repository. These tests keep the copies
from drifting: they must equal the files in k8s-bookinfo/tracing, the reviews-v2 Deployment must be the unmodified
original from bookinfo.yaml, and the expected Istio inventory must agree with the contract. Nothing contacts a cluster.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "extension" / "helper" / "internal"
EMBEDDED = HELPER / "traceinstall" / "manifests"
INVENTORY = HELPER / "traceinstall" / "data" / "istio-objects.json"
CONTRACT = HELPER / "bookinfotrace" / "changeset.json"
BOOKINFO = ROOT.parent / "k8s-bookinfo"

# embedded file -> its source in the Bookinfo repository
SOURCES = {
    "install-minimal.yaml": "tracing/istio/install-minimal.yaml",
    "serviceentry-jaeger-otlp.tmpl.yaml": "tracing/istio/serviceentry-jaeger-otlp.tmpl.yaml",
    "telemetry-bookinfo.yaml": "tracing/istio/telemetry-bookinfo.yaml",
    "reviews-v2-route.yaml": "tracing/istio/reviews-v2-route.yaml",
    "trace-demo-client.yaml": "tracing/traffic/trace-demo-client.yaml",
}

needs_bookinfo = pytest.mark.skipif(not (BOOKINFO / "tracing").is_dir(), reason="the k8s-bookinfo repository is not next to this one")


@needs_bookinfo
@pytest.mark.parametrize("name,source", sorted(SOURCES.items()))
def test_each_embedded_copy_is_identical_to_the_bookinfo_file(name, source):
    assert (EMBEDDED / name).read_bytes() == (BOOKINFO / source).read_bytes(), f"{name} drifted from {source}"


@needs_bookinfo
def test_the_embedded_reviews_v2_is_the_unmodified_original_deployment():
    original = [d for d in yaml.safe_load_all((BOOKINFO / "bookinfo.yaml").read_text(encoding="utf-8")) if d]
    (v2,) = [d for d in original if d["kind"] == "Deployment" and d["metadata"]["name"] == "reviews-v2"]
    embedded = yaml.safe_load((EMBEDDED / "reviews-v2.yaml").read_text(encoding="utf-8"))
    assert embedded == v2, "reviews-v2 must be exactly the Deployment in bookinfo.yaml"
    assert embedded["spec"]["template"]["spec"]["containers"][0]["image"].startswith("registry.istio.io/release/examples-bookinfo-reviews-v2:")


def test_the_expected_istio_inventory_agrees_with_the_contract():
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    objects = inventory["objects"]
    assert inventory["istioVersion"] == contract["istioVersion"]
    assert len(objects) == contract["install"]["renderedObjects"]
    assert sum(o["kind"] == "CustomResourceDefinition" for o in objects) == contract["install"]["customResourceDefinitions"]
    assert sum(o["kind"].endswith("WebhookConfiguration") for o in objects) == contract["install"]["webhooks"]
    keys = [(o["kind"], o["namespace"], o["name"]) for o in objects]
    assert len(keys) == len(set(keys)), "an object is listed twice"
    # nothing that could widen access beyond Istio's own control plane, and no gateway
    assert not [o for o in objects if "gateway" in o["name"] and o["kind"] in ("Deployment", "Service")]
    assert all(o["namespace"] in ("", "istio-system") for o in objects), "everything is cluster-scoped or in istio-system"


def test_pods_are_restarted_leaf_first():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert [w["app"] for w in contract["recreatedWorkloads"]] == ["ratings", "details", "productpage"]


def test_every_embedded_bookinfo_object_is_managed_and_in_the_bookinfo_namespace():
    for name in ("serviceentry-jaeger-otlp.tmpl.yaml", "telemetry-bookinfo.yaml", "reviews-v2-route.yaml", "trace-demo-client.yaml"):
        for doc in yaml.safe_load_all((EMBEDDED / name).read_text(encoding="utf-8")):
            assert doc["metadata"]["namespace"] == "bookinfo", name
            assert doc["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "resilience-tracing", name


def test_no_embedded_manifest_contains_a_literal_host_address():
    for path in EMBEDDED.glob("*.yaml"):
        text = re.sub(r"(?m)^\s*#.*$", "", path.read_text(encoding="utf-8")).replace("127.0.0.1", "")
        assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text), path.name
