import pytest

from app.engine.deployment_topology_modeling import service
from tests.deployment_topology_modeling import kind_snapshot


@pytest.fixture(autouse=True)
def stored_kind_cluster(monkeypatch):
    """No test in this package talks to a real cluster.

    The "live" cluster is the stored KinD snapshot, and declared intent comes from the tracked
    deploy/test-workloads manifests (not the git-ignored boutique.yaml), so results are the same
    on every machine.
    """
    monkeypatch.setattr(service, "collect_live_cluster", kind_snapshot.collect)
    monkeypatch.setattr(service, "DEFAULT_MANIFEST_PATH", service.FALLBACK_MANIFEST_DIR)
