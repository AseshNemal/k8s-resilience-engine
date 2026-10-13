from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.constants import ExposureLevel
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    ClusterExposureReport,
    PlacementDriftDiffReport,
    ServiceExposureResult,
)


class PlacementDriftDiffEngine:
    """
    Computes placement drift rate (D_rate) and state transitions between two 
    timestamped cluster snapshots (e.g. T0 baseline vs. T1 post-rollout/chaos).
    
    Formal Formulation:
        D_rate = |Services with changed placement/exposure| / |Total Services|
    """

    @staticmethod
    def diff_snapshots(
        baseline_snapshot: ClusterExposureReport | dict[str, Any],
        target_snapshot: ClusterExposureReport | dict[str, Any],
        baseline_id: str = "T0-baseline",
        target_id: str = "T1-current",
    ) -> PlacementDriftDiffReport:
        # Extract services map from report or dictionary
        def _get_services_map(snap: Any) -> dict[str, ServiceExposureResult]:
            if isinstance(snap, ClusterExposureReport):
                return {s.service_name: s for s in snap.services}
            elif isinstance(snap, dict):
                services_raw = snap.get("services", [])
                if isinstance(services_raw, list):
                    res = {}
                    for item in services_raw:
                        if isinstance(item, ServiceExposureResult):
                            res[item.service_name] = item
                        elif isinstance(item, dict):
                            res[item["service_name"]] = ServiceExposureResult(**item)
                    return res
                elif isinstance(services_raw, dict):
                    return {
                        k: v if isinstance(v, ServiceExposureResult) else ServiceExposureResult(**v)
                        for k, v in services_raw.items()
                    }
            return {}

        base_services = _get_services_map(baseline_snapshot)
        target_services = _get_services_map(target_snapshot)

        all_service_names = set(base_services.keys()).union(target_services.keys())
        if not all_service_names:
            return PlacementDriftDiffReport(
                baseline_snapshot_id=baseline_id,
                target_snapshot_id=target_id,
                total_services_compared=0,
                drifted_services_count=0,
                placement_drift_rate=0.0,
            )

        drifted_count = 0
        newly_exposed: list[str] = []
        remediated: list[str] = []
        state_transitions: dict[str, str] = {}

        for svc_name in sorted(all_service_names):
            base_exp = base_services.get(svc_name)
            target_exp = target_services.get(svc_name)

            if not base_exp and target_exp:
                # Newly deployed service
                drifted_count += 1
                state_transitions[svc_name] = f"NONE -> {target_exp.exposure_level.value}"
                if target_exp.exposure_level != ExposureLevel.RESILIENT:
                    newly_exposed.append(svc_name)
            elif base_exp and not target_exp:
                # Removed service
                drifted_count += 1
                state_transitions[svc_name] = f"{base_exp.exposure_level.value} -> REMOVED"
            elif base_exp and target_exp:
                base_level = base_exp.exposure_level
                target_level = target_exp.exposure_level
                base_conc = base_exp.replica_concentration
                target_conc = target_exp.replica_concentration

                is_drifted = (
                    base_level != target_level
                    or abs(base_conc - target_conc) > 0.05
                    or set(base_exp.exposed_failure_domains) != set(target_exp.exposed_failure_domains)
                )

                if is_drifted:
                    drifted_count += 1
                    transition_desc = f"{base_level.value} -> {target_level.value}"
                    state_transitions[svc_name] = transition_desc

                    if base_level == ExposureLevel.RESILIENT and target_level != ExposureLevel.RESILIENT:
                        newly_exposed.append(svc_name)
                    elif base_level != ExposureLevel.RESILIENT and target_level == ExposureLevel.RESILIENT:
                        remediated.append(svc_name)

        drift_rate = drifted_count / len(all_service_names) if all_service_names else 0.0

        return PlacementDriftDiffReport(
            baseline_snapshot_id=baseline_id,
            target_snapshot_id=target_id,
            total_services_compared=len(all_service_names),
            drifted_services_count=drifted_count,
            placement_drift_rate=round(drift_rate, 4),
            newly_exposed_services=newly_exposed,
            remediated_services=remediated,
            state_transitions=state_transitions,
        )
