"""The reviewed RBAC manifest grants exactly the read access the app needs, and nothing that writes."""

from pathlib import Path

import yaml

MANIFEST = Path(__file__).resolve().parents[1] / "deploy" / "rbac" / "resilience-readonly.yaml"


def cluster_role() -> dict:
    documents = [d for d in yaml.safe_load_all(MANIFEST.read_text()) if d]
    return next(d for d in documents if d["kind"] == "ClusterRole")


def test_role_grants_exactly_the_five_read_resources():
    granted = {
        f"{group}/{resource}"
        for rule in cluster_role()["rules"]
        for group in rule["apiGroups"]
        for resource in rule["resources"]
    }

    assert granted == {"/nodes", "/pods", "/services", "apps/deployments", "apps/replicasets"}


def test_role_only_reads():
    verbs = {verb for rule in cluster_role()["rules"] for verb in rule["verbs"]}

    assert verbs == {"get", "list"}


def test_role_never_reads_secrets_or_endpointslices():
    resources = {resource for rule in cluster_role()["rules"] for resource in rule["resources"]}

    assert not resources & {"secrets", "endpointslices", "configmaps", "*"}


ROOT = MANIFEST.parents[2]
DESCRIBING_THE_ACCESS = [
    "scripts/extension_cluster_access.py",
    "extension/ui-src/connect.js",
    "extension/build_ui.py",
]


def test_descriptions_of_the_access_name_services_and_replicasets():
    for relative in DESCRIBING_THE_ACCESS:
        text = (ROOT / relative).read_text()

        assert "services" in text and "replicasets" in text, relative
        assert "nodes, pods and deployments" not in text, relative


def test_setup_script_does_not_claim_it_leaves_existing_objects_alone():
    text = (ROOT / "scripts/extension_cluster_access.py").read_text()

    assert "no existing objects are modified" not in text
    assert "existing access objects are updated" in text.lower()
