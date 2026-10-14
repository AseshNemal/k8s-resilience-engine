"""
Deployment Topology Modeling API Endpoints (Member 2).
Categorized into 6 distinct research topics for Swagger documentation:
- Topic 1: Topology & Live Cluster State (Nodes, Zones, Services, Placements)
- Topic 2: Declared Manifest Intent (YAML parsing, Replicas, Constraints, Compliance)
- Topic 3: Resilience Drift & Failure Domain Exposure (HHI Concentration, SPOFs, 5 Detectors, Baseline B2)
- Topic 4: Chaos Engineering (8 Section 10 Disruption Scenarios, Markov Pod Migrations, Safety Guards)
- Topic 5: Cluster Snapshots & Cryptographic Provenance (SHA-256 Immutable State Freezing)
- Topic 6: Academic Benchmarks & Publications (>=98% Accuracy Certificate, LaTeX Tables, Markdown Reports)
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Response

from app.engine.deployment_topology_modeling.service import (
    get_sample_deployment,
    get_live_deployment_graph,
    get_manifest_intent,
    get_manifest_service_detail,
    analyze_resilience_drift,
    diff_cluster_snapshots,
    run_chaos_benchmark_pipeline,
    run_single_chaos_scenario,
    get_academic_accuracy_certificate,
    get_cluster_zones,
    get_node_detail,
    get_drift_spofs,
    get_drift_categories_summary,
    get_concentration_rankings,
    get_node_risk_rankings,
    list_cluster_snapshots,
    get_cluster_snapshot,
    freeze_cluster_state,
    clear_cluster_snapshots,
    get_chaos_safety_status,
    validate_chaos_action,
    get_scenario_latex_table,
)
from app.shared.schemas.deployment import DeploymentGraph
from experiments.placement_drift.disruption_orchestrator import DisruptionOrchestrator
from experiments.placement_drift.disruption_reporter import DisruptionReporter
from experiments.placement_drift.drift_transition_matrix import DriftTransitionMatrix
from experiments.placement_drift.snapshot_repository import SnapshotRepository


router = APIRouter(prefix="/api/deployments")

TAG_TOPOLOGY = "1. Topology & Live Cluster State"
TAG_MANIFEST = "2. Declared Manifest Intent"
TAG_DRIFT = "3. Resilience Drift & Failure Domain Exposure"
TAG_CHAOS = "4. Chaos Engineering (Disruption Scenarios)"
TAG_SNAPSHOTS = "5. Cluster Snapshots & Cryptographic Provenance"
TAG_BENCHMARKS = "6. Academic Benchmarks & Publications"


# =====================================================================
# Topic 1: Topology & Live Cluster State
# =====================================================================

@router.get(
    "/sample",
    response_model_by_alias=True,
    tags=[TAG_TOPOLOGY],
    summary="[Phase 1] Retrieve baseline static sample deployment graph",
)
def read_sample_deployment() -> DeploymentGraph:
    """Returns static golden reference topology model for offline testing."""
    return get_sample_deployment()


@router.get(
    "/live",
    response_model_by_alias=True,
    tags=[TAG_TOPOLOGY],
    summary="[Phase 1] Extract live Kubernetes cluster deployment topology",
)
def read_live_deployment(
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> DeploymentGraph:
    """Queries live Kubernetes API (Docker Desktop / KinD) and compiles topology graph."""
    return get_live_deployment_graph(namespace=namespace)


@router.get(
    "/topology/nodes",
    tags=[TAG_TOPOLOGY],
    summary="[Phase 1] List all cluster worker nodes with failure-domain zones & capacity",
)
def list_topology_nodes(
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> List[Dict[str, Any]]:
    """Lists resolved cluster worker nodes, their availability zones, and allocatable capacity."""
    graph = get_live_deployment_graph(namespace=namespace)
    return [
        {
            "name": n.name,
            "zone": n.zone,
            "cpu_available": n.cpu_available,
            "memory_available": n.memory_available,
        }
        for n in graph.nodes
    ]


@router.get(
    "/topology/nodes/{node_name}",
    tags=[TAG_TOPOLOGY],
    summary="[Phase 1] Inspect detailed workload placement for an individual worker node",
)
def get_node_topology_detail(
    node_name: str,
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> Dict[str, Any]:
    """Retrieves hosted microservices, pod list, and capacity for a specific worker node."""
    detail = get_node_detail(node_name=node_name, namespace=namespace)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Worker node '{node_name}' not found.")
    return detail


@router.get(
    "/topology/zones",
    tags=[TAG_TOPOLOGY],
    summary="[Phase 1] Summarize failure-domain zones and cross-zone pod distribution",
)
def list_topology_zones(
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> List[Dict[str, Any]]:
    """Returns cluster availability zones, member node counts, and hosted pod distribution."""
    return get_cluster_zones(namespace=namespace)


@router.get(
    "/topology/services",
    tags=[TAG_TOPOLOGY],
    summary="[Phase 1] List active microservices with replica counts & concentration index",
)
def list_topology_services(
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> List[Dict[str, Any]]:
    """Returns active microservices, their replica distribution, and concentration scores."""
    graph = get_live_deployment_graph(namespace=namespace)
    return [
        {
            "name": s.name,
            "declared_replicas": s.declared_replicas,
            "ready_replicas": s.ready_replicas,
            "replica_concentration": s.replica_concentration,
            "total_pods": len(s.pods),
            "hosting_nodes": list({p.node for p in s.pods}),
        }
        for s in graph.services
    ]


@router.get(
    "/topology/services/{service_name}",
    tags=[TAG_TOPOLOGY],
    summary="[Phase 1] Inspect detailed physical placement for a single microservice",
)
def get_service_topology_detail(
    service_name: str,
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> Dict[str, Any]:
    """Returns detailed pod-to-node placement mapping for a designated microservice."""
    graph = get_live_deployment_graph(namespace=namespace)
    svc = next((s for s in graph.services if s.name == service_name), None)
    if svc is None:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not found.")
    return {
        "service_name": svc.name,
        "declared_replicas": svc.declared_replicas,
        "ready_replicas": svc.ready_replicas,
        "replica_concentration": svc.replica_concentration,
        "pods": [
            {
                "pod_name": p.name,
                "node_name": p.node,
                "zone": p.zone,
                "is_ready": p.ready,
            }
            for p in svc.pods
        ],
    }


# =====================================================================
# Topic 2: Declared Manifest Intent
# =====================================================================

@router.get(
    "/manifests",
    tags=[TAG_MANIFEST],
    summary="[Phase 2] Parse declared YAML intent from deployment manifests",
)
def read_manifest_intent() -> List[Dict[str, Any]]:
    """Parses deploy/kubernetes/ manifests and extracts declared replicas, limits, and topology spread."""
    return get_manifest_intent()


@router.get(
    "/manifests/services",
    tags=[TAG_MANIFEST],
    summary="[Phase 2] List declared microservice deployment constraints",
)
def list_manifest_service_constraints() -> List[Dict[str, Any]]:
    """Extracts declared constraints (limits, nodeAffinity, topologySpreadConstraints) per service."""
    intent_data = get_manifest_intent()
    services_raw = intent_data.get("declared_services", intent_data.get("services", [])) if isinstance(intent_data, dict) else intent_data
    if isinstance(services_raw, dict):
        return [
            {"service_name": k, "constraints": v}
            for k, v in services_raw.items()
        ]
    return services_raw


@router.get(
    "/manifests/services/{service_name}",
    tags=[TAG_MANIFEST],
    summary="[Phase 2] Inspect declared constraints for an individual microservice",
)
def get_manifest_service_detail_endpoint(
    service_name: str,
) -> Dict[str, Any]:
    """Retrieves declared replica count, resource limits, and spread constraints for a service."""
    detail = get_manifest_service_detail(service_name=service_name)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not found in manifests.")
    return detail


@router.get(
    "/manifests/compliance",
    tags=[TAG_MANIFEST],
    summary="[Phase 2] Calculate overall constraint compliance rate",
)
def get_manifest_compliance_rate(
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> Dict[str, Any]:
    """Calculates percentage of live workloads strictly meeting their declared intent constraints."""
    report, _ = analyze_resilience_drift(namespace=namespace)
    return {
        "constraint_compliance_rate": report.constraint_compliance_rate,
        "total_services": report.total_services,
        "exposed_service_count": report.exposed_service_count,
        "mean_concentration": report.mean_concentration,
    }


# =====================================================================
# Topic 3: Resilience Drift & Failure Domain Exposure
# =====================================================================

@router.get(
    "/drift",
    tags=[TAG_DRIFT],
    summary="[Phase 3] Compute cluster resilience drift & failure-domain exposure report",
)
def get_resilience_drift(
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> Dict[str, Any]:
    """Evaluates live placement against declared intent; returns exposure levels and drift violations."""
    report, _ = analyze_resilience_drift(namespace=namespace)
    return report.model_dump()


@router.get(
    "/drift/violations",
    tags=[TAG_DRIFT],
    summary="[Phase 3] List all active resilience drift violations across all 5 detectors",
)
def list_drift_violations(
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> List[Dict[str, Any]]:
    """Extracts all active violations from scale, resource, affinity, contention, and spread detectors."""
    report, _ = analyze_resilience_drift(namespace=namespace)
    violations: List[Dict[str, Any]] = []
    for svc in report.services:
        for v in getattr(svc, "violations", []):
            item = v.model_dump() if hasattr(v, "model_dump") else dict(v)
            item["service_name"] = svc.service_name
            violations.append(item)
    return violations


@router.get(
    "/drift/categories",
    tags=[TAG_DRIFT],
    summary="[Phase 3] Categorical breakdown of violations across all 5 detectors",
)
def get_drift_categories(
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> Dict[str, Any]:
    """Groups active violations into scale, resource limit, node affinity, contention, and spread."""
    return get_drift_categories_summary(namespace=namespace)


@router.get(
    "/drift/spofs",
    tags=[TAG_DRIFT],
    summary="[Phase 3] Identify Single Points of Failure (SPOF) with 100% replica colocation",
)
def list_drift_spofs(
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> List[Dict[str, Any]]:
    """Lists services where all replicas are colocated on a single node or zone."""
    return get_drift_spofs(namespace=namespace)


@router.get(
    "/drift/concentration",
    tags=[TAG_DRIFT],
    summary="[Phase 3] Rank microservices by Herfindahl-Hirschman Concentration Index (HHI)",
)
def list_concentration_rankings(
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> List[Dict[str, Any]]:
    """Sorts all microservices descending by replica concentration index (0.0=perfect spread, 1.0=colocated)."""
    return get_concentration_rankings(namespace=namespace)


@router.get(
    "/drift/nodes/risk",
    tags=[TAG_DRIFT],
    summary="[Phase 3] Rank cluster worker nodes by failure blast radius and workload density",
)
def list_node_risk_rankings(
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> List[Dict[str, Any]]:
    """Ranks worker nodes by proportion of cluster workloads hosted and failure risk tier."""
    return get_node_risk_rankings(namespace=namespace)


@router.get(
    "/drift/exposure/{service_name}",
    tags=[TAG_DRIFT],
    summary="[Phase 3] Inspect failure domain exposure for an individual service",
)
def get_service_exposure_detail(
    service_name: str,
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> Dict[str, Any]:
    """Calculates single-service availability score SA(s, {n}) under failure scenarios."""
    report, _ = analyze_resilience_drift(namespace=namespace)
    match = next((s for s in report.services if s.service_name == service_name), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not evaluated in drift report.")
    return match.model_dump()


@router.get(
    "/baseline-b2",
    tags=[TAG_DRIFT],
    summary="[Phase 3] Retrieve Baseline B2 deployment-only vulnerability ranking",
)
def get_baseline_b2(
    namespace: str = Query("default", description="Target Kubernetes namespace"),
) -> Dict[str, Any]:
    """Calculates Baseline B2 deployment-only failure exposure scores for control comparison."""
    _, baseline_b2 = analyze_resilience_drift(namespace=namespace)
    return baseline_b2.model_dump()


@router.post(
    "/diff",
    tags=[TAG_DRIFT],
    summary="[Phase 3] Diff two cluster snapshots to calculate Placement Drift Rate (D_rate)",
)
def diff_snapshots(
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Calculates placement drift transition dynamics and Drate between baseline and target states."""
    baseline = payload.get("baseline", {})
    target = payload.get("target", {})
    diff_report = diff_cluster_snapshots(baseline, target)
    return diff_report.model_dump()


# =====================================================================
# Topic 4: Chaos Engineering (Disruption Scenarios)
# =====================================================================

@router.get(
    "/chaos/scenarios",
    tags=[TAG_CHAOS],
    summary="[Phase 4] Catalog of all 8 Section 10 controlled disruption scenarios",
)
def list_chaos_scenarios() -> List[Dict[str, Any]]:
    """Returns metadata for all 8 controlled Kubernetes disruption scenarios from the Study Guide."""
    scenarios = []
    for cls in DisruptionOrchestrator.ALL_SCENARIO_CLASSES:
        dummy = cls()
        scenarios.append({
            "scenario_id": dummy.scenario_id,
            "scenario_name": dummy.scenario_name,
            "disruption_type": dummy.disruption_type.value,
            "target_service": dummy.target_service,
            "description": cls.__doc__.strip() if cls.__doc__ else "",
        })
    return scenarios


@router.get(
    "/chaos/scenarios/{scenario_id}",
    tags=[TAG_CHAOS],
    summary="[Phase 4] Inspect scenario specification, parameters, and rollback design",
)
def get_chaos_scenario_detail(
    scenario_id: str,
) -> Dict[str, Any]:
    """Retrieves target parameters and lifecycle specifications for a single chaos scenario."""
    for cls in DisruptionOrchestrator.ALL_SCENARIO_CLASSES:
        dummy = cls()
        if dummy.scenario_id == scenario_id:
            return {
                "scenario_id": dummy.scenario_id,
                "scenario_name": dummy.scenario_name,
                "disruption_type": dummy.disruption_type.value,
                "target_service": dummy.target_service,
                "namespace": dummy.namespace,
                "execution_mode": dummy.execution_mode.value,
                "description": cls.__doc__.strip() if cls.__doc__ else "",
            }
    raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")


@router.post(
    "/chaos/run/{scenario_id}",
    tags=[TAG_CHAOS],
    summary="[Phase 4] Execute a single designated disruption scenario by ID",
)
def run_chaos_scenario(
    scenario_id: str,
    namespace: str = Query("default", description="Target namespace"),
) -> Dict[str, Any]:
    """Injects a specific disruption fault (e.g. node drain, pod crash) and calculates placement drift."""
    result = run_single_chaos_scenario(scenario_id=scenario_id, namespace=namespace)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")
    return result.model_dump()


@router.get(
    "/chaos/matrix/{scenario_id}",
    tags=[TAG_CHAOS],
    summary="[Phase 4] Get Markov pod migration transition matrix for a scenario",
)
def get_scenario_transition_matrix(
    scenario_id: str,
) -> Dict[str, Any]:
    """Builds and formats the Markov pod transition grid T[i, j] between T0 and T1 snapshots."""
    repo = SnapshotRepository()
    orchestrator = DisruptionOrchestrator(repository=repo)
    result = orchestrator.run_scenario(scenario_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    t0 = repo.get(scenario_id, "T0")
    t1 = repo.get(scenario_id, "T1")
    if not t0 or not t1:
        raise HTTPException(status_code=400, detail="Snapshot data missing for scenario.")

    nodes, matrix, records = DriftTransitionMatrix.build_matrix(t0, t1)
    ascii_table = DriftTransitionMatrix.format_ascii_table(nodes, matrix)
    latex_table = DriftTransitionMatrix.format_latex_table(nodes, matrix)

    return {
        "scenario_id": scenario_id,
        "nodes": nodes,
        "transition_matrix": matrix,
        "ascii_table": ascii_table,
        "latex_table": latex_table,
        "migration_records": [r.model_dump() for r in records],
        "d_rate": result.d_rate,
        "delta_e_rate": result.delta_e_rate,
    }


@router.get(
    "/chaos/safety/status",
    tags=[TAG_CHAOS],
    summary="[Phase 4] Inspect pre-flight safety guard status and protected namespaces",
)
def get_safety_status() -> Dict[str, Any]:
    """Returns protected system namespaces, active execution mode, and multi-node feasibility."""
    return get_chaos_safety_status()


@router.post(
    "/chaos/safety/validate",
    tags=[TAG_CHAOS],
    summary="[Phase 4] Validate proposed chaos fault against safety guardrails",
)
def validate_proposed_chaos_action(
    namespace: str = Query("default", description="Target namespace"),
    service_name: Optional[str] = Query(None, description="Target service"),
) -> Dict[str, Any]:
    """Validates that a proposed disruption fault does not violate system namespace or safety rules."""
    return validate_chaos_action(namespace=namespace, service_name=service_name)


# =====================================================================
# Topic 5: Cluster Snapshots & Cryptographic Provenance
# =====================================================================

@router.get(
    "/snapshots",
    tags=[TAG_SNAPSHOTS],
    summary="[Phase 4] List all recorded cluster snapshots in repository",
)
def list_snapshots() -> List[str]:
    """Returns all registered snapshot compound keys (e.g. '07_node_drain_eviction_T0')."""
    return list_cluster_snapshots()


@router.get(
    "/snapshots/{scenario_id}/{phase_tag}",
    tags=[TAG_SNAPSHOTS],
    summary="[Phase 4] Retrieve an immutable cluster snapshot with SHA-256 verification",
)
def get_snapshot_detail(
    scenario_id: str,
    phase_tag: str,
) -> Dict[str, Any]:
    """Retrieves snapshot and cryptographically validates that placement matches the SHA-256 hash."""
    snapshot = get_cluster_snapshot(scenario_id=scenario_id, phase_tag=phase_tag)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot for scenario '{scenario_id}' phase '{phase_tag}' not found in repository.",
        )
    return snapshot


@router.post(
    "/snapshots/freeze",
    tags=[TAG_SNAPSHOTS],
    summary="[Phase 4] Freeze current live cluster placement into an immutable SHA-256 snapshot",
)
def freeze_live_snapshot(
    snapshot_id: str = Query("manual_snapshot", description="Identifier for this snapshot"),
    namespace: str = Query("default", description="Target namespace"),
) -> Dict[str, Any]:
    """Captures live pod placement and computes a deterministic SHA-256 fingerprint."""
    return freeze_cluster_state(snapshot_id=snapshot_id, namespace=namespace)


@router.delete(
    "/snapshots",
    tags=[TAG_SNAPSHOTS],
    summary="[Phase 4] Reset and clear in-memory cluster snapshot cache",
)
def clear_snapshots_cache() -> Dict[str, Any]:
    """Clears in-memory snapshots cache."""
    return clear_cluster_snapshots()


# =====================================================================
# Topic 6: Academic Benchmarks & Publications
# =====================================================================

@router.get(
    "/chaos/benchmarks",
    tags=[TAG_BENCHMARKS],
    summary="[Academic Defense] Run all 8 disruption scenarios and compile benchmark metrics",
)
def get_chaos_benchmarks(
    namespace: str = Query("default", description="Target namespace for chaos simulation"),
) -> Dict[str, Any]:
    """Executes all 8 Section 10 disruption scenarios and returns D_rate, delta_E_rate, and transitions."""
    return run_chaos_benchmark_pipeline(namespace=namespace)


@router.get(
    "/benchmarks/accuracy",
    tags=[TAG_BENCHMARKS],
    summary="[Academic Defense] Model Accuracy Certificate (>=98% Mapping, >=95% Concentration)",
)
def get_accuracy_certificate() -> Dict[str, Any]:
    """Evaluates ground-truth accuracy metrics required by the viva evaluation panel."""
    cert = get_academic_accuracy_certificate()
    return cert.model_dump() if hasattr(cert, "model_dump") else dict(cert)


@router.get(
    "/benchmarks/report/markdown",
    tags=[TAG_BENCHMARKS],
    summary="[Academic Defense] Export publication-grade Markdown benchmark report",
)
def get_markdown_benchmark_report() -> Response:
    """Returns formatted Markdown benchmark table with dissertation defense observations."""
    orchestrator = DisruptionOrchestrator()
    results = orchestrator.run_all()
    metrics = orchestrator.compute_aggregate_metrics(results)
    md_content = DisruptionReporter.generate_markdown_report(results, metrics)
    return Response(content=md_content, media_type="text/markdown")


@router.get(
    "/benchmarks/report/json",
    tags=[TAG_BENCHMARKS],
    summary="[Academic Defense] Export full publication-grade JSON benchmark dataset",
)
def get_json_benchmark_report(
    namespace: str = Query("default", description="Target namespace"),
) -> Dict[str, Any]:
    """Returns serialized JSON benchmark dataset with aggregate metrics and per-scenario telemetry."""
    return run_chaos_benchmark_pipeline(namespace=namespace)


@router.get(
    "/benchmarks/latex/{scenario_id}",
    tags=[TAG_BENCHMARKS],
    summary="[Academic Defense] Export publication-ready LaTeX transition matrix table",
)
def get_latex_transition_table(
    scenario_id: str,
) -> Dict[str, Any]:
    """Returns LaTeX table code snippet for the Markov transition grid T[i, j] of a scenario."""
    res = get_scenario_latex_table(scenario_id=scenario_id)
    if res is None:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found or snapshot missing.")
    return res