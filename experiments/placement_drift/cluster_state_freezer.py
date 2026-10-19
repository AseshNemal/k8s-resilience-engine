"""
Cluster State Freezer: Captures immutable, SHA-256 cryptographic snapshots of cluster placement.
Component: Component 2 - Deployment Topology Modeling
"""
import hashlib
import json
from typing import Any, Dict, List

from experiments.placement_drift.scenario_models import (
    ClusterSnapshot,
    PodPlacementRecord,
)


class ClusterStateFreezer:
    """
    Freezes cluster placement state into deterministic, immutable snapshots.
    Generates cryptographic SHA-256 hashes to guarantee experiment auditability.
    """

    @staticmethod
    def compute_placement_hash(pods: List[PodPlacementRecord]) -> str:
        """
        Computes a deterministic SHA-256 fingerprint from pod-to-node placement.
        Sorts records by pod_name to ensure hash consistency across identical states.
        """
        canonical_items = [
            {
                "pod": p.pod_name,
                "service": p.service_name,
                "node": p.node_name,
                "zone": p.zone,
                "ready": p.is_ready,
            }
            for p in sorted(pods, key=lambda x: x.pod_name)
        ]
        serialized = json.dumps(canonical_items, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def normalize_pod_records(cls, raw_pods: List[Any]) -> List[PodPlacementRecord]:
        """Converts raw pod dictionaries or objects into validated PodPlacementRecord DTOs."""
        records: List[PodPlacementRecord] = []
        for p in raw_pods:
            if isinstance(p, PodPlacementRecord):
                records.append(p.model_copy(deep=True))
            elif isinstance(p, dict):
                records.append(
                    PodPlacementRecord(
                        pod_name=p.get("name", p.get("pod_name", "unknown-pod")),
                        service_name=p.get("service_name", p.get("service", "unknown-service")),
                        node_name=p.get("node_name", p.get("node", "unassigned")),
                        zone=p.get("zone"),
                        is_ready=bool(p.get("is_ready", p.get("ready", False))),
                    )
                )
            else:
                records.append(
                    PodPlacementRecord(
                        pod_name=getattr(p, "name", getattr(p, "pod_name", "unknown-pod")),
                        service_name=getattr(p, "service_name", getattr(p, "service", "unknown-service")),
                        node_name=getattr(p, "node_name", getattr(p, "node", "unassigned")),
                        zone=getattr(p, "zone", None),
                        is_ready=bool(getattr(p, "is_ready", getattr(p, "ready", False))),
                    )
                )
        return records

    @classmethod
    def freeze(
        cls,
        snapshot_id: str,
        raw_pods: List[Any],
        nodes: List[Dict[str, Any]] | None = None,
        namespace: str = "default",
    ) -> ClusterSnapshot:
        """Constructs an immutable ClusterSnapshot with cryptographic SHA-256 hash."""
        nodes = nodes or []
        pod_records = cls.normalize_pod_records(raw_pods)
        state_hash = cls.compute_placement_hash(pod_records)
        unique_services = {p.service_name for p in pod_records}

        return ClusterSnapshot(
            snapshot_id=snapshot_id,
            sha256_hash=state_hash,
            namespace=namespace,
            pods=pod_records,
            nodes=nodes,
            total_pods=len(pod_records),
            total_services=len(unique_services),
        )
