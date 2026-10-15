"""
Component 2 Dedicated Endpoints Router.

Provides component-internal HTTP endpoints for Phase 3 Resilience Drift Engine:
- GET /drift: Detailed cluster failure-domain exposure report
- GET /baseline-b2: Experimental control baseline B2 service rankings
- POST /diff: State diffing and placement drift rate (D_rate) calculation
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from app.engine.deployment_topology_modeling.service import (
    analyze_resilience_drift,
    diff_cluster_snapshots,
    run_chaos_benchmark_pipeline,
    run_single_chaos_scenario,
)

from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    ClusterExposureReport,
    BaselineB2Result,
    PlacementDriftDiffReport,
)


router = APIRouter(
    prefix="/engine/deployment-topology",
    tags=["Deployment Topology Modeling Engine"],
)


@router.get(
    "/drift",
    response_model_by_alias=True,
    summary="Compute live cluster resilience drift and failure-domain exposure",
)
def get_resilience_drift_report(
    namespace: str = "default",
) -> ClusterExposureReport:
    """Evaluates runtime pod placement against declared constraints to detect drift."""
    report, _ = analyze_resilience_drift(namespace=namespace)
    return report


@router.get(
    "/baseline-b2",
    response_model_by_alias=True,
    summary="Retrieve Baseline B2 deployment-only vulnerability ranking",
)
def get_baseline_b2_ranking(
    namespace: str = "default",
) -> BaselineB2Result:
    """Computes Baseline B2 for experimental evaluation and control comparisons."""
    _, baseline_b2 = analyze_resilience_drift(namespace=namespace)
    return baseline_b2


@router.post(
    "/diff",
    response_model_by_alias=True,
    summary="Diff two cluster snapshots to calculate D_rate and state transitions",
)
def diff_snapshots_endpoint(
    payload: Dict[str, Any],
) -> PlacementDriftDiffReport:
    """Calculates placement drift transition dynamics between baseline and target snapshots."""
    baseline = payload.get("baseline", {})
    target = payload.get("target", {})
    return diff_cluster_snapshots(baseline, target)


@router.get(
    "/chaos/benchmarks",
    summary="Execute all 8 Section 10 chaos scenarios and return benchmark metrics",
)
def get_chaos_benchmarks(
    namespace: str = "default",
) -> Dict[str, Any]:
    """Runs all 8 controlled disruption scenarios and calculates aggregate D_rate benchmarks."""
    return run_chaos_benchmark_pipeline(namespace=namespace)


@router.post(
    "/chaos/run/{scenario_id}",
    summary="Execute an isolated Section 10 disruption scenario by ID",
    responses={404: {"description": "Scenario not found"}},
)
def run_chaos_scenario_endpoint(
    scenario_id: str,
    namespace: str = "default",
) -> Dict[str, Any]:
    """Runs a single designated disruption fault scenario and returns transition results."""
    result = run_single_chaos_scenario(scenario_id=scenario_id, namespace=namespace)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")
    return result.model_dump()

