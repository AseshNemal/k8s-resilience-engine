import json
import time
from typing import Any
from dataclasses import asdict
from pathlib import Path
from app.shared.schemas.deployment import DeploymentGraph

# Core Domain Dependencies
from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import get_cluster_nodes, get_cluster_pods
from app.engine.deployment_topology_modeling.live_state.ownership_resolver import resolve_deployment_ownership
from app.engine.deployment_topology_modeling.live_state.concentration_analyzer import analyze_replica_concentration

# Internal JSON Mapping and Transformation Dependencies
from app.engine.deployment_topology_modeling.graph_builder import DeploymentGraphBuilder


# Phase 2 Manifest Intent Pipeline
from app.engine.deployment_topology_modeling.manifest_parsing.pipeline import run_manifest_pipeline

# Phase 3 Resilience Drift Analysis Dependencies
from app.engine.deployment_topology_modeling.drift_analysis.drift_analyzer import DriftAnalyzer
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    ClusterExposureReport,
    BaselineB2Result,
    PlacementDriftDiffReport,
)
from app.engine.deployment_topology_modeling.drift_analysis.placement_drift_diff_engine import (
    PlacementDriftDiffEngine,
)


from app.engine.deployment_topology_modeling.live_state.cluster_models import ClusterNode


def _get_nodes_and_workloads(
    namespace: str = "default",
) -> tuple[list[ClusterNode], list[dict[str, Any]]]:
    """
    Extracts live nodes and analyzed workloads.
    
    If the Kubernetes cluster control plane is not reachable (e.g. offline testing or local dev),
    gracefully falls back to the canonical sample deployment topology.
    """
    try:
        raw_nodes = get_cluster_nodes()
        raw_pods = get_cluster_pods(namespace=namespace)
        if raw_nodes:
            workloads = resolve_deployment_ownership(pods=raw_pods, namespace=namespace)
            analyzed_services = analyze_replica_concentration(workloads)
            return raw_nodes, analyzed_services
    except Exception:
        pass

    sample_graph = get_sample_deployment()
    fallback_nodes = [
        ClusterNode(
            name=n.name,
            zone=n.zone,
            cpu_available=str(n.cpu_available),
            memory_available=str(n.memory_available),
        )
        for n in sample_graph.nodes
    ]
    fallback_services = [
        {
            "name": s.name,
            "declaredReplicas": s.declared_replicas,
            "readyReplicas": s.ready_replicas,
            "replicaConcentration": s.replica_concentration,
            "pods": [
                {
                    "name": p.name,
                    "node": p.node,
                    "zone": p.zone,
                    "ready": p.ready,
                }
                for p in s.pods
            ],
        }
        for s in sample_graph.services
    ]
    return fallback_nodes, fallback_services


def get_live_deployment_graph(
    namespace: str = "default",
    manifest_path: str | None = None,
) -> DeploymentGraph:
    """
    Central Orchestrator for Deployment Topology Modeling.
    
    Acts as the primary API facade for Component 2, orchestrating live cluster state,
    declared manifest parsing, and Phase 3 resilience drift analysis.
    """
    start_time = time.perf_counter()
    
    raw_nodes, analyzed_services = _get_nodes_and_workloads(namespace=namespace)

    # Resolve declared manifest intent if available
    project_root = Path(__file__).resolve().parents[3]
    default_manifest = project_root / "deploy" / "kubernetes" / "boutique.yaml"
    target_manifest = Path(manifest_path) if manifest_path else default_manifest

    manifest_map = {}
    if target_manifest.exists():
        try:
            parsed_manifests = run_manifest_pipeline(str(target_manifest))
            manifest_map = {m.workload_name: m for m in parsed_manifests}
        except Exception:
            manifest_map = {}

    # Structure pods by service for the Phase 3 Drift Engine
    pods_by_service = {
        svc.get("name", ""): svc.get("pods", [])
        for svc in analyzed_services
        if svc.get("name")
    }
    nodes_dicts = [
        {"name": n.name, "zone": n.zone, "labels": {"kubernetes.io/hostname": n.name}}
        for n in raw_nodes
    ]

    # Run Phase 3 Resilience Drift Engine
    analyzer = DriftAnalyzer()
    cluster_report, _ = analyzer.analyze_cluster(
        manifests_by_service=manifest_map,
        runtime_pods_by_service=pods_by_service,
        cluster_nodes=nodes_dicts,
    )

    # Map drift violations to service models
    drift_reports = {}
    for exp in cluster_report.services:
        drift_reports[exp.service_name] = {
            "drift_detected": len(exp.violations) > 0 or exp.severity_score > 0.5,
            "drift_reasons": [v.description for v in exp.violations],
        }

    # Build the strict JSON DeploymentGraph
    builder = DeploymentGraphBuilder()
    builder.attach_nodes(raw_nodes)
    builder.attach_services(analyzed_services, drift_reports)
    
    elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
    return builder.build(runtime_overhead_ms=elapsed_ms)


def analyze_resilience_drift(
    namespace: str = "default",
    manifest_path: str | None = None,
) -> tuple[ClusterExposureReport, BaselineB2Result]:
    """Public interface for Component 3/4 to retrieve cluster exposure and baseline B2."""
    project_root = Path(__file__).resolve().parents[3]
    default_manifest = project_root / "deploy" / "kubernetes" / "boutique.yaml"
    target_manifest = Path(manifest_path) if manifest_path else default_manifest

    manifest_map = {}
    if target_manifest.exists():
        try:
            parsed_manifests = run_manifest_pipeline(str(target_manifest))
            manifest_map = {m.workload_name: m for m in parsed_manifests}
        except Exception:
            manifest_map = {}

    raw_nodes, analyzed_services = _get_nodes_and_workloads(namespace=namespace)

    pods_by_service = {
        svc.get("name", ""): svc.get("pods", [])
        for svc in analyzed_services
        if svc.get("name")
    }
    nodes_dicts = [
        {"name": n.name, "zone": n.zone, "labels": {"kubernetes.io/hostname": n.name}}
        for n in raw_nodes
    ]

    analyzer = DriftAnalyzer()
    return analyzer.analyze_cluster(
        manifests_by_service=manifest_map,
        runtime_pods_by_service=pods_by_service,
        cluster_nodes=nodes_dicts,
    )


def diff_cluster_snapshots(
    baseline_snapshot: ClusterExposureReport | dict,
    target_snapshot: ClusterExposureReport | dict,
) -> PlacementDriftDiffReport:
    """Public interface to diff two cluster snapshots (D_rate calculation)."""
    return PlacementDriftDiffEngine.diff_snapshots(baseline_snapshot, target_snapshot)


def get_sample_deployment() -> DeploymentGraph:
    """Provides fallback static JSON mocking for independent UI interface testing."""
    project_root = Path(__file__).resolve().parents[3]
    sample_file = project_root / "sample_data" / "deployment.json"

    if sample_file.exists():
        with open(sample_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return DeploymentGraph.model_validate(data)

    return DeploymentGraph(nodes=[], services=[])


if __name__ == "__main__":
    print("--- LIVE DEPLOYMENT GRAPH MODEL (ENTERPRISE EDITION) ---")
    graph = get_live_deployment_graph()
    print(f"Total Worker Nodes Resolved: {len(graph.nodes)}")
    print(f"Total Active Services Found: {len(graph.services)}")