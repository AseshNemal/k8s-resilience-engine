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

    @classmethod
    def _parse_services_map(cls, snap: Any) -> dict[str, ServiceExposureResult]:
        """Extracts and normalizes service exposure results into a keyed dictionary."""
        if isinstance(snap, ClusterExposureReport):
            return {s.service_name: s for s in snap.services}
        if not isinstance(snap, dict):
            return {}

        services_raw = snap.get("services", [])
        if isinstance(services_raw, list):
            res: dict[str, ServiceExposureResult] = {}
            for item in services_raw:
                if isinstance(item, ServiceExposureResult):
                    res[item.service_name] = item
                elif isinstance(item, dict):
                    res[item["service_name"]] = ServiceExposureResult(**item)
            return res
        if isinstance(services_raw, dict):
            return {
                k: v if isinstance(v, ServiceExposureResult) else ServiceExposureResult(**v)
                for k, v in services_raw.items()
            }
        return {}

    @staticmethod
    def _has_drift(base_exp: ServiceExposureResult, target_exp: ServiceExposureResult) -> bool:
        """Determines if placement or exposure level drifted between snapshots."""
        if base_exp.exposure_level != target_exp.exposure_level:
            return True
        if abs(base_exp.replica_concentration - target_exp.replica_concentration) > 0.05:
            return True
        return set(base_exp.exposed_failure_domains) != set(target_exp.exposed_failure_domains)

    @classmethod
    def _diff_existing_service(
        cls,
        base_exp: ServiceExposureResult,
        target_exp: ServiceExposureResult,
    ) -> tuple[bool, str | None, bool, bool]:
        """
        Calculates drift metrics for a service present in both snapshots.
        Returns: (is_drifted, transition_description, is_newly_exposed, is_remediated)
        """
        if not cls._has_drift(base_exp, target_exp):
            return False, None, False, False

        base_level = base_exp.exposure_level
        target_level = target_exp.exposure_level
        transition_desc = f"{base_level.value} -> {target_level.value}"

        is_newly_exposed = (
            base_level == ExposureLevel.RESILIENT and target_level != ExposureLevel.RESILIENT
        )
        is_remediated = (
            base_level != ExposureLevel.RESILIENT and target_level == ExposureLevel.RESILIENT
        )
        return True, transition_desc, is_newly_exposed, is_remediated

    @classmethod
    def _evaluate_service_transition(
        cls,
        base_exp: ServiceExposureResult | None,
        target_exp: ServiceExposureResult | None,
    ) -> tuple[bool, str | None, bool, bool]:
        """
        Evaluates transition dynamics across snapshots for any service.
        Returns: (is_drifted, transition_str, is_newly_exposed, is_remediated)
        """
        if not base_exp and target_exp:
            is_exposed = target_exp.exposure_level != ExposureLevel.RESILIENT
            return True, f"NONE -> {target_exp.exposure_level.value}", is_exposed, False

        if base_exp and not target_exp:
            return True, f"{base_exp.exposure_level.value} -> REMOVED", False, False

        if base_exp and target_exp:
            return cls._diff_existing_service(base_exp, target_exp)

        return False, None, False, False

    @classmethod
    def diff_snapshots(
        cls,
        baseline_snapshot: ClusterExposureReport | dict[str, Any],
        target_snapshot: ClusterExposureReport | dict[str, Any],
        baseline_id: str = "T0-baseline",
        target_id: str = "T1-current",
    ) -> PlacementDriftDiffReport:
        base_services = cls._parse_services_map(baseline_snapshot)
        target_services = cls._parse_services_map(target_snapshot)

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
            is_drifted, trans, is_new, is_rem = cls._evaluate_service_transition(
                base_services.get(svc_name), target_services.get(svc_name)
            )
            if not is_drifted:
                continue

            drifted_count += 1
            if trans:
                state_transitions[svc_name] = trans
            if is_new:
                newly_exposed.append(svc_name)
            elif is_rem:
                remediated.append(svc_name)

        drift_rate = round(drifted_count / len(all_service_names), 4)

        return PlacementDriftDiffReport(
            baseline_snapshot_id=baseline_id,
            target_snapshot_id=target_id,
            total_services_compared=len(all_service_names),
            drifted_services_count=drifted_count,
            placement_drift_rate=drift_rate,
            newly_exposed_services=newly_exposed,
            remediated_services=remediated,
            state_transitions=state_transitions,
        )
