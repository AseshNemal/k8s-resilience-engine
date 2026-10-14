"""
Snapshot Repository: Stores, indexes, and validates SHA-256 integrity of chaos snapshots.
Component: Component 2 - Deployment Topology Modeling
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from experiments.placement_drift.cluster_state_freezer import ClusterStateFreezer
from experiments.placement_drift.exceptions import SnapshotPersistenceError
from experiments.placement_drift.scenario_models import ClusterSnapshot

logger = logging.getLogger(__name__)


class SnapshotRepository:
    """
    Manages in-memory caching and optional filesystem persistence for cluster snapshots.
    Guarantees snapshot integrity via SHA-256 re-verification upon retrieval.
    """

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir
        self._memory_store: Dict[str, ClusterSnapshot] = {}

        if self.storage_dir:
            self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save(self, scenario_id: str, phase_tag: str, snapshot: ClusterSnapshot) -> str:
        """
        Saves a snapshot indexed by scenario_id and phase_tag (e.g. ('02_scale_up', 'T0')).
        Returns the compound storage key.
        """
        key = f"{scenario_id}_{phase_tag}"
        self._memory_store[key] = snapshot

        if self.storage_dir:
            try:
                target_file = self.storage_dir / f"{key}.json"
                with open(target_file, "w", encoding="utf-8") as f:
                    f.write(snapshot.model_dump_json(indent=2))
                logger.debug("Persisted snapshot '%s' to disk: %s", key, target_file)
            except Exception as exc:
                raise SnapshotPersistenceError(
                    f"Failed to persist snapshot '{key}' to disk: {exc}"
                ) from exc

        return key

    def get(self, scenario_id: str, phase_tag: str) -> Optional[ClusterSnapshot]:
        """Retrieves and integrity-checks a snapshot."""
        key = f"{scenario_id}_{phase_tag}"

        # Check memory first
        if key in self._memory_store:
            snapshot = self._memory_store[key]
            self.verify_integrity(snapshot)
            return snapshot

        # Check disk if storage_dir is configured
        if self.storage_dir:
            target_file = self.storage_dir / f"{key}.json"
            if target_file.exists():
                try:
                    with open(target_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    snapshot = ClusterSnapshot(**data)
                    self.verify_integrity(snapshot)
                    self._memory_store[key] = snapshot
                    return snapshot
                except Exception as exc:
                    raise SnapshotPersistenceError(
                        f"Failed to load snapshot file '{target_file}': {exc}"
                    ) from exc

        return None

    def verify_integrity(self, snapshot: ClusterSnapshot) -> bool:
        """Verifies that the stored SHA-256 hash matches the current pod placement."""
        computed = ClusterStateFreezer.compute_placement_hash(snapshot.pods)
        if computed != snapshot.sha256_hash:
            raise SnapshotPersistenceError(
                f"Snapshot Integrity Tamper Detected for '{snapshot.snapshot_id}'. "
                f"Expected hash: {snapshot.sha256_hash}, computed hash: {computed}."
            )
        return True

    def list_keys(self) -> List[str]:
        """Returns all registered snapshot index keys."""
        return list(self._memory_store.keys())

    def clear(self) -> None:
        """Clears in-memory snapshot cache."""
        self._memory_store.clear()
