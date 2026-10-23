"""The reviewed RBAC manifest grants the full access to the connected research cluster that Apply and Reset need (developer-approved 2026-10-23)."""

from pathlib import Path

import yaml

MANIFEST = Path(__file__).resolve().parents[1] / "deploy" / "rbac" / "resilience-readonly.yaml"


def cluster_role() -> dict:
    documents = [d for d in yaml.safe_load_all(MANIFEST.read_text()) if d]
    return next(d for d in documents if d["kind"] == "ClusterRole")


def test_role_is_one_full_access_rule_for_the_connected_cluster():
    rules = cluster_role()["rules"]

    assert rules == [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]


def test_role_keeps_its_old_name_so_an_existing_install_upgrades_in_place():
    documents = [d for d in yaml.safe_load_all(MANIFEST.read_text()) if d]

    assert {d["metadata"]["name"] for d in documents if d["kind"] in {"ClusterRole", "ClusterRoleBinding", "ServiceAccount"}} == {
        "resilience-engine-reader"
    }


ROOT = MANIFEST.parents[2]
DESCRIBING_THE_ACCESS = [
    "scripts/extension_cluster_access.py",
    "extension/ui-src/connect.js",
    "extension/build_ui.py",
]


def test_no_description_of_the_access_still_says_it_is_read_only():
    for relative in DESCRIBING_THE_ACCESS:
        text = (ROOT / relative).read_text().lower()

        assert "read-only access" not in text and "cannot change your cluster" not in text, relative
        assert "full access" in text, relative


def test_setup_script_does_not_claim_it_leaves_existing_objects_alone():
    text = (ROOT / "scripts/extension_cluster_access.py").read_text()

    assert "no existing objects are modified" not in text
    assert "existing access objects are updated" in text.lower()
