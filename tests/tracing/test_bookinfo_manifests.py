"""The Bookinfo tracing manifests (k8s-bookinfo/tracing) agree with each other and with the helper's allow-list.

The manifests live in the user's own Bookinfo repository, a sibling of this one. When it is not there (CI, another
machine) these tests skip. Nothing here contacts a cluster: files are parsed, never applied.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BOOKINFO = ROOT.parent / "k8s-bookinfo"
TRACING = BOOKINFO / "tracing"
CONTRACT = ROOT / "extension" / "helper" / "internal" / "bookinfotrace" / "changeset.json"

pytestmark = pytest.mark.skipif(not TRACING.is_dir(), reason="the k8s-bookinfo repository is not next to this one")

ALLOWED_KINDS = {"IstioOperator", "ServiceEntry", "Telemetry", "DestinationRule", "VirtualService", "Pod"}


def documents(relative: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((TRACING / relative).read_text(encoding="utf-8")) if d]


def one(relative: str, kind: str, name: str | None = None) -> dict:
    found = [d for d in documents(relative) if d["kind"] == kind and (name is None or d["metadata"]["name"] == name)]
    assert len(found) == 1, f"{relative}: expected one {kind} {name}, found {len(found)}"
    return found[0]


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def all_manifests() -> list[tuple[str, dict]]:
    return [(str(p.relative_to(TRACING)), d) for p in sorted(TRACING.rglob("*.yaml")) for d in yaml.safe_load_all(p.read_text(encoding="utf-8")) if d]


def test_every_object_in_the_helpers_allow_list_exists_in_its_file(contract):
    for entry in contract["objects"]:
        doc = one(entry["source"], entry["kind"], entry["name"])
        assert doc["metadata"]["namespace"] == contract["namespace"], entry
        assert doc["metadata"]["labels"]["app.kubernetes.io/managed-by"] == contract["managedBy"], entry


def test_every_object_the_manifests_create_is_in_the_allow_list(contract):
    allowed = {(o["kind"], o["name"]) for o in contract["objects"]}
    for relative, doc in all_manifests():
        if doc["kind"] == "IstioOperator":
            continue
        assert (doc["kind"], doc["metadata"]["name"]) in allowed, f"{relative}: {doc['kind']}/{doc['metadata']['name']} is not in the allow-list"
        assert doc["metadata"]["namespace"] == contract["namespace"], "everything but Istio itself stays in the bookinfo namespace"


def test_only_the_expected_kinds_are_used_and_nothing_is_cluster_wide():
    kinds = {doc["kind"] for _, doc in all_manifests()}
    assert kinds <= ALLOWED_KINDS, kinds
    for relative, doc in all_manifests():
        assert "namespace" in doc["metadata"], f"{relative}: {doc['kind']} has no namespace"


def test_the_install_matches_the_pinned_version_the_profile_and_the_caps(contract):
    install = one(contract["install"]["source"], "IstioOperator")["spec"]
    assert install["profile"] == "minimal"
    assert install["tag"] == contract["istioVersion"] and install["hub"] == "docker.io/istio"
    assert "gateways" not in json.dumps(install).lower(), "the minimal install must not mention gateways"
    pilot = install["components"]["pilot"]["k8s"]
    assert pilot["replicaCount"] == 1 and pilot["resources"]["limits"]["memory"] == f"{contract['memoryMiB']['istiodLimit']}Mi"
    proxy = install["values"]["global"]["proxy"]["resources"]["limits"]["memory"]
    assert proxy == f"{contract['memoryMiB']['sidecarLimit']}Mi"
    assert install["values"]["pilot"]["autoscaleEnabled"] is False


def test_the_provider_the_service_entry_and_the_telemetry_agree():
    install = one("istio/install-minimal.yaml", "IstioOperator")["spec"]["meshConfig"]
    assert install["enableTracing"] is True and len(install["extensionProviders"]) == 1
    provider = install["extensionProviders"][0]
    entry = one("istio/serviceentry-jaeger-otlp.tmpl.yaml", "ServiceEntry")["spec"]
    telemetry = one("istio/telemetry-bookinfo.yaml", "Telemetry")["spec"]

    assert provider["opentelemetry"]["service"] in entry["hosts"]
    assert provider["opentelemetry"]["port"] == entry["ports"][0]["number"] == 4317
    assert entry["ports"][0]["protocol"] == "GRPC"
    assert telemetry["tracing"][0]["providers"][0]["name"] == provider["name"]
    assert telemetry["tracing"][0]["randomSamplingPercentage"] == 100


def test_tracing_is_scoped_to_the_bookinfo_namespace_only():
    telemetry = one("istio/telemetry-bookinfo.yaml", "Telemetry")
    assert telemetry["metadata"]["namespace"] == "bookinfo"
    entry = one("istio/serviceentry-jaeger-otlp.tmpl.yaml", "ServiceEntry")["spec"]
    assert entry["exportTo"] == ["."], "the Jaeger entry must be visible to its own namespace only"
    assert entry["location"] == "MESH_EXTERNAL" and entry["resolution"] == "STATIC"


def test_no_docker_host_address_is_hardcoded_anywhere():
    template = (TRACING / "istio/serviceentry-jaeger-otlp.tmpl.yaml").read_text(encoding="utf-8")
    assert re.search(r"address:\s*__JAEGER_OTLP_IPV4__\s*$", template, re.M), "the address must be the placeholder"
    for path in TRACING.rglob("*.yaml"):
        text = re.sub(r"(?m)^\s*#.*$", "", path.read_text(encoding="utf-8"))
        # 127.0.0.1 inside the Pod is the sidecar's own admin port (how the client stops it), not a host address.
        text = text.replace("127.0.0.1", "")
        assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text), f"{path.name} contains a literal IPv4 address"


def test_every_demo_request_is_routed_to_reviews_v2():
    rule = one("istio/reviews-v2-route.yaml", "DestinationRule")["spec"]
    route = one("istio/reviews-v2-route.yaml", "VirtualService")["spec"]
    assert {s["name"]: s["labels"] for s in rule["subsets"]} == {"v1": {"version": "v1"}, "v2": {"version": "v2"}}
    assert rule["host"] == route["hosts"][0] == "reviews.bookinfo.svc.cluster.local"
    (http,) = route["http"]
    (destination,) = http["route"]
    assert destination["destination"]["subset"] == "v2" and "weight" not in destination, "one destination, no split, no weights"


def test_the_client_pod_sends_real_requests_and_is_pinned_and_bounded():
    pod = one("traffic/trace-demo-client.yaml", "Pod")
    spec = pod["spec"]
    container = spec["containers"][0]
    assert spec["restartPolicy"] == "Never"
    assert container["image"].startswith("curlimages/curl:") and not container["image"].endswith(":latest")
    assert "limits" in container["resources"] and container["resources"]["limits"]["memory"]
    assert json.loads(pod["metadata"]["annotations"]["proxy.istio.io/config"])["holdApplicationUntilProxyStarts"] is True
    script = "\n".join(container["args"])
    assert "productpage.bookinfo.svc.cluster.local:9080/productpage" in script
    assert "quitquitquit" in script, "the sidecar must be stopped so the Pod can finish"
    for forbidden in ("v1/traces", "resourceSpans", "4317", "4318", "jaeger"):
        assert forbidden not in script.lower(), f"the client must not talk to Jaeger ({forbidden})"


def test_the_contract_matches_the_memory_and_version_facts_in_the_readme(contract):
    readme = (TRACING / "README.md").read_text(encoding="utf-8")
    assert contract["istioVersion"] in readme
    assert f"{contract['install']['renderedObjects']} objects" in readme
    assert f"{contract['memoryMiB']['istiodLimit']} Mi" in readme
    for gate in ("300 MiB", "2.6 GiB", "3.2 GiB", "1 GiB"):
        assert gate in readme, gate


def test_the_original_bookinfo_files_are_not_edited():
    branch = subprocess.run(["git", "-C", str(BOOKINFO), "branch", "--show-current"], capture_output=True, text=True)
    if branch.returncode != 0 or branch.stdout.strip() == "main":
        pytest.skip("not on a preparation branch")
    committed = subprocess.run(["git", "-C", str(BOOKINFO), "diff", "--name-only", "main...HEAD"], capture_output=True, text=True)
    pending = subprocess.run(["git", "-C", str(BOOKINFO), "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True)
    assert committed.returncode == 0 and pending.returncode == 0
    files = [line for line in committed.stdout.splitlines() if line] + [line[3:] for line in pending.stdout.splitlines() if line]
    assert files, "the preparation branch has no changes"
    outside = [f for f in files if not f.startswith("tracing/")]
    assert outside == [], f"files outside tracing/ were changed: {outside}"
    dirty = subprocess.run(["git", "-C", str(BOOKINFO), "status", "--porcelain", "--", "bookinfo.yaml", "networking", "platform", "src", "gateway-api", "policy", "DEPLOY.md", "README.md"], capture_output=True, text=True)
    assert dirty.stdout.strip() == "", f"original files have uncommitted edits: {dirty.stdout}"
