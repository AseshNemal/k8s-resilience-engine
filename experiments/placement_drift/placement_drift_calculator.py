"""
Placement drift metrics for scenario snapshots, using the single definitions in
app/engine/deployment_topology_modeling/placement_metrics.py:

    D_rate       = share of services (present in T0 and T1) whose exposure status changed
    E_rate       = exposed services / services with at least one ready replica
    Delta_E_rate = E_rate(T1) - E_rate(T0)
"""
from typing import Dict, List

from app.engine.deployment_topology_modeling import placement_metrics as pm
from experiments.placement_drift.scenario_models import ClusterSnapshot


class PlacementDriftCalculator:

    @staticmethod
    def exposure_statuses(snapshot: ClusterSnapshot) -> Dict[str, pm.ExposureStatus]:
        pods_by_service: Dict[str, list] = {}
        for p in snapshot.pods:
            pods_by_service.setdefault(p.service_name, []).append(
                {"name": p.pod_name, "node": p.node_name, "ready": p.is_ready}
            )
        return {svc: pm.exposure_status(pods) for svc, pods in pods_by_service.items()}

    @classmethod
    def drift(cls, t0_snapshot: ClusterSnapshot, t1_snapshot: ClusterSnapshot) -> pm.PlacementDriftResult:
        return pm.placement_drift(cls.exposure_statuses(t0_snapshot), cls.exposure_statuses(t1_snapshot))

    @classmethod
    def compute_drift_rate(cls, t0_snapshot: ClusterSnapshot, t1_snapshot: ClusterSnapshot) -> float | None:
        rate = cls.drift(t0_snapshot, t1_snapshot).rate
        return round(rate, 4) if rate is not None else None

    @classmethod
    def compute_exposure_rate(cls, snapshot: ClusterSnapshot) -> float | None:
        rate = pm.exposure_rate(cls.exposure_statuses(snapshot).values())
        return round(rate, 4) if rate is not None else None

    @classmethod
    def compute_delta_exposure_rate(cls, t0_snapshot: ClusterSnapshot, t1_snapshot: ClusterSnapshot) -> float | None:
        e0 = cls.compute_exposure_rate(t0_snapshot)
        e1 = cls.compute_exposure_rate(t1_snapshot)
        if e0 is None or e1 is None:
            return None
        return round(e1 - e0, 4)

    @classmethod
    def get_drifted_services(cls, t0_snapshot: ClusterSnapshot, t1_snapshot: ClusterSnapshot) -> List[str]:
        return cls.drift(t0_snapshot, t1_snapshot).changed_services
