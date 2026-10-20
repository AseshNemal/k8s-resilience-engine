import pytest

from app.engine.deployment_topology_modeling import service
from tests.deployment_topology_modeling import kind_snapshot


@pytest.fixture(autouse=True)
def stored_kind_cluster(request, monkeypatch):
    """No test in this package talks to a real cluster.

    A test that asks for ``use_cluster`` installs its own fake cluster and opts out of the snapshot, so
    it exercises the same cluster seam the extension's discovery uses.

    The "live" cluster is the stored KinD snapshot, and declared intent comes from the tracked
    deploy/test-workloads manifests (not the git-ignored boutique.yaml), so results are the same
    on every machine.
    """
    if "use_cluster" in request.fixturenames:
        return
    monkeypatch.setattr(service, "collect_live_cluster", kind_snapshot.collect)
    monkeypatch.setattr(service, "DEFAULT_MANIFEST_PATH", service.FALLBACK_MANIFEST_DIR)
