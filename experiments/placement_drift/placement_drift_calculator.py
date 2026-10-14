"""
Placement Drift Calculator: Formal mathematical formulations of Drate, Erate, and delta-Erate.
Component: Component 2 - Deployment Topology Modeling
"""
from typing import Dict, List, Set

from experiments.placement_drift.scenario_models import ClusterSnapshot, PodPlacementRecord


class PlacementDriftCalculator:
    """
    Mathematical engine calculating formal placement drift metrics:
        D_rate = |Services with changed placement| / |Total Services|
        E_rate = |Exposed Services| / |Total Services|
        Delta_E_rate = E_rate(T1) - E_rate(T0)
    """

    @classmethod
    def compute_drift_rate(
        cls, t0_snapshot: ClusterSnapshot, t1_snapshot: ClusterSnapshot
    ) -> float:
        """Computes the fraction of services whose physical placement shifted."""
        t0_svcs = cls._group_pods_by_service(t0_snapshot.pods)
        t1_svcs = cls._group_pods_by_service(t1_snapshot.pods)

        all_services = set(t0_svcs.keys()).union(t1_svcs.keys())
        if not all_services:
            return 0.0

        drifted_count = 0
        for svc in all_services:
            nodes_t0 = t0_svcs.get(svc, set())
            nodes_t1 = t1_svcs.get(svc, set())
            if nodes_t0 != nodes_t1:
                drifted_count += 1

        return round(drifted_count / len(all_services), 4)

    @classmethod
    def compute_exposure_rate(cls, snapshot: ClusterSnapshot) -> float:
        """
        Calculates E_rate: percentage of services concentrated on a single failure domain.
        """
        svc_map = cls._group_pods_by_service(snapshot.pods)
        if not svc_map:
            return 0.0

        exposed_count = 0
        for svc, nodes in svc_map.items():
            if len(nodes) == 1:
                exposed_count += 1

        return round(exposed_count / len(svc_map), 4)

    @classmethod
    def compute_delta_exposure_rate(
        cls, t0_snapshot: ClusterSnapshot, t1_snapshot: ClusterSnapshot
    ) -> float:
        """Computes net change in failure domain exposure rate: Delta_E = E(T1) - E(T0)."""
        e0 = cls.compute_exposure_rate(t0_snapshot)
        e1 = cls.compute_exposure_rate(t1_snapshot)
        return round(e1 - e0, 4)

    @classmethod
    def get_drifted_services(
        cls, t0_snapshot: ClusterSnapshot, t1_snapshot: ClusterSnapshot
    ) -> List[str]:
        """Identifies names of services whose placement shifted between T0 and T1."""
        t0_svcs = cls._group_pods_by_service(t0_snapshot.pods)
        t1_svcs = cls._group_pods_by_service(t1_snapshot.pods)
        all_services = set(t0_svcs.keys()).union(t1_svcs.keys())
        return [
            svc for svc in sorted(all_services)
            if t0_svcs.get(svc, set()) != t1_svcs.get(svc, set())
        ]

    @staticmethod
    def _group_pods_by_service(
        pods: List[PodPlacementRecord],
    ) -> Dict[str, Set[str]]:
        """Groups assigned worker nodes by service name."""
        grouped: Dict[str, Set[str]] = {}
        for p in pods:
            if p.service_name not in grouped:
                grouped[p.service_name] = set()
            grouped[p.service_name].add(p.node_name)
        return grouped
