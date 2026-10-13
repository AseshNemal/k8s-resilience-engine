from datetime import datetime, timezone
from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import DriftViolation


class DriftAuditTrail:
    """
    Immutable, timestamped audit log recorder for drift evaluation runs.
    
    Ensures research reproducibility and traceability for each detected 
    resilience violation and placement state change.
    """

    def __init__(self):
        self._records: list[dict[str, Any]] = []

    def record_run(
        self,
        cluster_name: str,
        total_services: int,
        exposed_count: int,
        exposure_rate: float,
        compliance_rate: float,
    ) -> dict[str, Any]:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "DRIFT_ANALYSIS_RUN",
            "cluster_name": cluster_name,
            "total_services": total_services,
            "exposed_count": exposed_count,
            "exposure_rate": exposure_rate,
            "compliance_rate": compliance_rate,
        }
        self._records.append(entry)
        return entry

    def record_violation(
        self,
        service_name: str,
        violation: DriftViolation,
    ) -> dict[str, Any]:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "VIOLATION_DETECTED",
            "service_name": service_name,
            "drift_type": violation.drift_type.value,
            "severity": violation.severity.value,
            "description": violation.description,
            "expected": violation.expected,
            "actual": violation.actual,
            "affected_domain": violation.affected_failure_domain,
        }
        self._records.append(entry)
        return entry

    def get_records(self, service_name: str | None = None) -> list[dict[str, Any]]:
        if service_name:
            return [r for r in self._records if r.get("service_name") == service_name]
        return list(self._records)

    def clear(self) -> None:
        self._records.clear()
