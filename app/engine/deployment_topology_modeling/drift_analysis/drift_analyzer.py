import time
from datetime import datetime, timezone
from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.drift_config import DriftConfig
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    DriftViolation,
    ServiceExposureResult,
    ClusterExposureReport,
    BaselineB2Result,
)
from app.engine.deployment_topology_modeling.drift_analysis.constants import ExposureLevel
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
    """
    Central Orchestrator for Phase 3 Resilience Drift & Placement Exposure Modeling.
    
    Coordinates the detector suite, failure domain simulations, audit logging,
    and Baseline B2 ranking generation.
    """

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
        # Run all specialized detectors
        violations: list[DriftViolation] = []
        for detector in self.detectors:
            det_violations = detector.detect(
                service_name=service_name,
                declared_intent=declared_intent,
                runtime_pods=runtime_pods,
                cluster_nodes=cluster_nodes,
            )
            violations.extend(det_violations)

            if self.config.enable_audit_logging:
                for v in det_violations:
                    self.audit_trail.record_violation(service_name, v)

        # Run single failure-domain evaluation
        exposure_result = self.exposure_engine.evaluate_service_exposure(
            service_name=service_name,
            runtime_pods=runtime_pods,
            cluster_nodes=cluster_nodes,
            violations=violations,
        )
        return exposure_result

    def analyze_cluster(
        self,
        manifests_by_service: dict[str, Any],
        runtime_pods_by_service: dict[str, list[dict[str, Any]]],
        cluster_nodes: list[dict[str, Any]],
        cluster_name: str = "production-cluster",
    ) -> tuple[ClusterExposureReport, BaselineB2Result]:
        start_time = time.perf_counter()

        all_services = sorted(
            set(manifests_by_service.keys()).union(runtime_pods_by_service.keys())
        )
        total_services_count = len(all_services)

        service_exposures: list[ServiceExposureResult] = []
        compliant_services_count = 0
        exposed_services_count = 0
        total_concentration = 0.0

        for svc in all_services:
            declared = manifests_by_service.get(svc)
            pods = runtime_pods_by_service.get(svc, [])
            exposure = self.analyze_service(
                service_name=svc,
                declared_intent=declared,
                runtime_pods=pods,
                cluster_nodes=cluster_nodes,
            )
            service_exposures.append(exposure)

            total_concentration += exposure.replica_concentration

            # Compliance means no violations detected
            if len(exposure.violations) == 0:
                compliant_services_count += 1

            # Exposed if FULL_EXPOSURE or SINGLE_REPLICA_EXPOSURE
            if exposure.exposure_level in (
                ExposureLevel.FULL_EXPOSURE,
                ExposureLevel.SINGLE_REPLICA_EXPOSURE,
            ):
                exposed_services_count += 1

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        exposure_rate = (
            exposed_services_count / total_services_count
            if total_services_count > 0
            else 0.0
        )
        compliance_rate = (
            compliant_services_count / total_services_count
            if total_services_count > 0
            else 1.0
        )
        mean_conc = (
            total_concentration / total_services_count
            if total_services_count > 0
            else 0.0
        )

        cluster_report = ClusterExposureReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_services=total_services_count,
            exposed_service_count=exposed_services_count,
            exposure_rate=round(exposure_rate, 4),
            constraint_compliance_rate=round(compliance_rate, 4),
            mean_concentration=round(mean_conc, 4),
            services=service_exposures,
            runtime_overhead_ms=round(elapsed_ms, 2),
        )

        baseline_b2 = self.baseline_engine.generate_baseline_b2(service_exposures)

        if self.config.enable_audit_logging:
            self.audit_trail.record_run(
                cluster_name=cluster_name,
                total_services=total_services_count,
                exposed_count=exposed_services_count,
                exposure_rate=cluster_report.exposure_rate,
                compliance_rate=cluster_report.constraint_compliance_rate,
            )

        return cluster_report, baseline_b2
