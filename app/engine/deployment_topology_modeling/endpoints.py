"""
Component 2 Dedicated Endpoints Router.
Owned by: Member 2 (P.A.M. Minara).

Provides component-internal HTTP endpoints for Phase 3 Resilience Drift Engine:
- GET /drift: Detailed cluster failure-domain exposure report
- GET /baseline-b2: Experimental control baseline B2 service rankings
- POST /diff: State diffing and placement drift rate (D_rate) calculation
"""
from fastapi import APIRouter
from typing import Dict, Any

from app.engine.deployment_topology_modeling.service import (
    analyze_resilience_drift,
    diff_cluster_snapshots,
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
    response_model=ClusterExposureReport,
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
    response_model=BaselineB2Result,
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
    response_model=PlacementDriftDiffReport,
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
