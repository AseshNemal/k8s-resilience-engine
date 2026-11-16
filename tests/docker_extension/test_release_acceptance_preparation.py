"""Non-destructive acceptance preparation; no kubeconfig or cluster is used."""
import json
from pathlib import Path

import pytest
import yaml

from scripts import release_acceptance as release


def test_prepare_is_pinned_private_and_cannot_overwrite_evidence(tmp_path):
    bundle = tmp_path / "acceptance"
    plan = release.prepare(bundle, "test-operator")
    assert plan["writesAuthorized"] is False and plan["faultInjectionAuthorized"] is False
    assert "@sha256:" in plan["nodeImage"] and "@sha256:" in plan["workloadImage"]
    docs = list(yaml.safe_load_all((bundle / "workloads.yaml").read_text()))
    assert all(x["metadata"].get("namespace", release.NAMESPACE) == release.NAMESPACE for x in docs)
    assert [n["role"] for n in yaml.safe_load((bundle / "kind.yaml").read_text())["nodes"]] == ["control-plane", "worker", "worker"]
    assert not (bundle / "kubeconfig").exists() and not (bundle / "acceptance.db").exists()
    with pytest.raises(FileExistsError):
        release.prepare(bundle, "another-operator")


def test_wrong_cluster_uid_stops_preflight_before_any_namespace_read(tmp_path, monkeypatch):
    from app.integrations.kubernetes import validation_gateway
    class Gateway:
        def __init__(self, path, **kwargs):
            assert not kwargs.get("allow_writes")
        def identity(self):
            from types import SimpleNamespace
            return SimpleNamespace(cluster_uid="wrong", context=f"kind-{release.NAME}")
        def _get(self, *args):
            pytest.fail("read proceeded after UID mismatch")
    monkeypatch.setattr(validation_gateway, "KubernetesTestClusterGateway", Gateway)
    with pytest.raises(ValueError, match="UID"):
        release.check(tmp_path, "expected")
