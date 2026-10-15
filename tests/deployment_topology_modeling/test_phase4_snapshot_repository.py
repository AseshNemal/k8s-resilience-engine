import pytest
from pathlib import Path
from experiments.placement_drift.cluster_state_freezer import ClusterStateFreezer
from experiments.placement_drift.exceptions import SnapshotPersistenceError
from experiments.placement_drift.scenario_models import ClusterSnapshot, PodPlacementRecord
from experiments.placement_drift.snapshot_repository import SnapshotRepository


def _create_sample_snapshot(snapshot_id: str = "snap-test") -> ClusterSnapshot:
    pods = [
        PodPlacementRecord(
            pod_name="frontend-1",
            service_name="frontend",
            namespace="chaos",
            node_name="node-1",
        ),
        PodPlacementRecord(
            pod_name="cart-1",
            service_name="cart",
            namespace="chaos",
            node_name="node-2",
        ),
    ]
    computed_hash = ClusterStateFreezer.compute_placement_hash(pods)
    return ClusterSnapshot(
        snapshot_id=snapshot_id,
        sha256_hash=computed_hash,
        namespace="chaos",
        pods=pods,
        nodes=[{"name": "node-1"}, {"name": "node-2"}],
        total_pods=2,
        total_services=2,
    )


def test_repository_save_and_get_memory():
    repo = SnapshotRepository()
    snapshot = _create_sample_snapshot("s1")

    key = repo.save("01_baseline", "T0", snapshot)
    assert key == "01_baseline_T0"
    assert "01_baseline_T0" in repo.list_keys()

    retrieved = repo.get("01_baseline", "T0")
    assert retrieved is not None
    assert retrieved.snapshot_id == "s1"
    assert retrieved.sha256_hash == snapshot.sha256_hash


def test_repository_save_and_get_disk(tmp_path: Path):
    storage_dir = tmp_path / "snapshots"
    repo = SnapshotRepository(storage_dir=storage_dir)
    snapshot = _create_sample_snapshot("s2")

    key = repo.save("02_scale_up", "T1", snapshot)
    disk_file = storage_dir / f"{key}.json"
    assert disk_file.exists()

    # Clear memory cache to force disk load
    repo.clear()
    assert len(repo.list_keys()) == 0

    retrieved = repo.get("02_scale_up", "T1")
    assert retrieved is not None
    assert retrieved.snapshot_id == "s2"


def test_repository_tamper_detection():
    repo = SnapshotRepository()
    snapshot = _create_sample_snapshot("s3")
    # Artificially tamper with the hash
    tampered_snapshot = snapshot.model_copy(update={"sha256_hash": "tampered_fake_hash"})

    repo.save("03_scale_down", "T0", tampered_snapshot)

    with pytest.raises(SnapshotPersistenceError, match="Snapshot Integrity Tamper Detected"):
        repo.get("03_scale_down", "T0")


def test_repository_clear():
    repo = SnapshotRepository()
    snapshot = _create_sample_snapshot("s4")
    repo.save("04_rolling", "T0", snapshot)
    assert len(repo.list_keys()) == 1

    repo.clear()
    assert len(repo.list_keys()) == 0
    assert repo.get("04_rolling", "T0") is None
