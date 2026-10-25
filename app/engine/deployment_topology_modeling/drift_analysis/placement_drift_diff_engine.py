from typing import Any
from app.engine.deployment_topology_modeling import placement_metrics as pm
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    ClusterExposureReport,
    PlacementDriftDiffReport,
    ServiceExposureResult,
)


class PlacementDriftDiffEngine:
    """Placement drift rate between two snapshots (Study Guide section 12):

        D_rate = |{s in both snapshots : status_T0(s) != status_T1(s)}| / |services in both snapshots|

    where status is the binary-plus-unavailable ExposureStatus. Services that appear or disappear
    between snapshots are listed separately and are not part of D_rate.
    """

    @staticmethod
    def _status_of(item: Any) -> pm.ExposureStatus:
        if isinstance(item, ServiceExposureResult):
            return item.exposure_status
        return ServiceExposureResult.model_validate(item).exposure_status

    @classmethod
    def _parse_statuses(cls, snap: Any) -> dict[str, pm.ExposureStatus]:
        if isinstance(snap, ClusterExposureReport):
            return {s.service_name: s.exposure_status for s in snap.services}
        if not isinstance(snap, dict):
            return {}
        services_raw = snap.get("services", [])
        if isinstance(services_raw, dict):
            return {name: cls._status_of(item) for name, item in services_raw.items()}
        statuses = {}
        for item in services_raw:
            name = item.service_name if isinstance(item, ServiceExposureResult) else item["service_name"]
            statuses[name] = cls._status_of(item)
        return statuses

    @classmethod
    def diff_snapshots(
        cls,
        baseline_snapshot: ClusterExposureReport | dict[str, Any],
        target_snapshot: ClusterExposureReport | dict[str, Any],
        baseline_id: str = "T0-baseline",
        target_id: str = "T1-current",
    ) -> PlacementDriftDiffReport:
        # Exposure status per service at T0 and at T1, then the shared D_rate definition.
        before = cls._parse_statuses(baseline_snapshot)
        after = cls._parse_statuses(target_snapshot)
        drift = pm.placement_drift(before, after)
        exposed = pm.ExposureStatus.EXPOSED

        return PlacementDriftDiffReport(
            baseline_snapshot_id=baseline_id,
            target_snapshot_id=target_id,
            total_services_compared=len(drift.compared_services),
            drifted_services_count=len(drift.changed_services),
            placement_drift_rate=round(drift.rate, 4) if drift.rate is not None else None,
            drifted_services=drift.changed_services,
            # got worse (now exposed) / got better (was exposed, now safe)
            newly_exposed_services=[s for s in drift.changed_services if after[s] == exposed],
            remediated_services=[
                s for s in drift.changed_services
                if before[s] == exposed and after[s] == pm.ExposureStatus.NOT_EXPOSED
            ],
            added_services=drift.added_services,
            removed_services=drift.removed_services,
            state_transitions=drift.transitions,
        )
