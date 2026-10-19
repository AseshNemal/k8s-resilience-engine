import time
from datetime import datetime, timezone
from typing import Any
from app.engine.deployment_topology_modeling import placement_metrics as pm
from app.engine.deployment_topology_modeling.drift_analysis import intent_access as ia
from app.engine.deployment_topology_modeling.drift_analysis.drift_config import DriftConfig
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    DriftViolation,
    ServiceExposureResult,
    ClusterExposureReport,
    BaselineB2Result,
)
from app.engine.deployment_topology_modeling.drift_analysis.scale_drift_detector import ScaleDriftDetector
from app.engine.deployment_topology_modeling.drift_analysis.affinity_drift_detector import AffinityDriftDetector
from app.engine.deployment_topology_modeling.drift_analysis.topology_spread_detector import (
    TopologySpreadDriftDetector,
)
from app.engine.deployment_topology_modeling.drift_analysis.resource_contention_detector import (
    ResourceContentionDetector,
)
from app.engine.deployment_topology_modeling.drift_analysis.failure_domain_exposure_engine import (
    FailureDomainExposureEngine,
)
from app.engine.deployment_topology_modeling.drift_analysis.deployment_only_baseline_engine import (
    DeploymentOnlyBaselineEngine,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_audit_trail import DriftAuditTrail


class DriftAnalyzer:
    """Runs the detectors and the exposure engine per service and aggregates cluster metrics."""

    def __init__(self, config: DriftConfig | None = None):
        self.config = config or DriftConfig()
        self.detectors = [
            ScaleDriftDetector(self.config),
            AffinityDriftDetector(self.config),
            TopologySpreadDriftDetector(self.config),
            ResourceContentionDetector(self.config),
        ]
        self.exposure_engine = FailureDomainExposureEngine(self.config)
        self.baseline_engine = DeploymentOnlyBaselineEngine()
        self.audit_trail = DriftAuditTrail()

    def analyze_service(
        self,
        service_name: str,
        declared_intent: Any | None,
        runtime_pods: list[dict[str, Any]],
        cluster_nodes: list[dict[str, Any]],
    ) -> ServiceExposureResult:
        violations: list[DriftViolation] = []
        for detector in self.detectors:
            found = detector.detect(
                service_name=service_name,
                declared_intent=declared_intent,
                runtime_pods=runtime_pods,
                cluster_nodes=cluster_nodes,
            )
            violations.extend(found)
            if self.config.enable_audit_logging:
                for v in found:
                    self.audit_trail.record_violation(service_name, v)

        return self.exposure_engine.evaluate_service_exposure(
            service_name=service_name,
            runtime_pods=runtime_pods,
            violations=violations,
            has_placement_rules=ia.has_placement_rules(declared_intent),
        )

    def analyze_cluster(
        self,
        manifests_by_service: dict[str, Any],
        runtime_pods_by_service: dict[str, list[dict[str, Any]]],
        cluster_nodes: list[dict[str, Any]],
        cluster_name: str = "production-cluster",
    ) -> tuple[ClusterExposureReport, BaselineB2Result]:
        """Services are the runtime services; a manifest with no runtime service is not evaluated,
        because exposure is a property of running replicas."""
        start_time = time.perf_counter()

        exposures = [
            self.analyze_service(
                service_name=svc,
                declared_intent=manifests_by_service.get(svc),
                runtime_pods=runtime_pods_by_service.get(svc, []),
                cluster_nodes=cluster_nodes,
            )
            for svc in sorted(runtime_pods_by_service)
        ]

        statuses = [e.exposure_status for e in exposures]
        rate = pm.exposure_rate(statuses)
        compliance = pm.compliance_rate({e.service_name: e.placement_rules_satisfied for e in exposures})
        ratios = [e.replica_concentration for e in exposures if e.replica_concentration is not None]
        exposed = [e.service_name for e in exposures if e.exposure_status == pm.ExposureStatus.EXPOSED]

        report = ClusterExposureReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_services=len(exposures),
            evaluated_services=sum(s != pm.ExposureStatus.UNAVAILABLE for s in statuses),
            unavailable_services=[
                e.service_name for e in exposures if e.exposure_status == pm.ExposureStatus.UNAVAILABLE
            ],
            exposed_services=exposed,
            exposed_service_count=len(exposed),
            exposure_rate=round(rate, 4) if rate is not None else None,
            services_with_placement_rules=sum(e.has_placement_rules for e in exposures),
            constraint_compliance_rate=round(compliance, 4) if compliance is not None else None,
            mean_concentration=round(sum(ratios) / len(ratios), 4) if ratios else None,
            services=exposures,
            runtime_overhead_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
        )

        baseline_b2 = self.baseline_engine.generate_baseline_b2(exposures)

        if self.config.enable_audit_logging:
            self.audit_trail.record_run(
                cluster_name=cluster_name,
                total_services=report.total_services,
                exposed_count=report.exposed_service_count,
                exposure_rate=report.exposure_rate,
                compliance_rate=report.constraint_compliance_rate,
            )

        return report, baseline_b2
