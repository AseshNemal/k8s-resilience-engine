from typing import Any
from fastapi import APIRouter, HTTPException, Query

from app.engine.workflow_dependency_modeling.evaluator import (
    evaluate_graph,
    run_sensitivity_analysis,
)
from app.engine.workflow_dependency_modeling.service import (
    discover_live_workflows,
    get_live_workflow,
    get_live_evidence,
    get_sample_workflow,
    get_workflow_by_id,
    get_workflow_definition,
    list_benchmark_workflows,
)
from app.integrations.jaeger.client import DEFAULT_JAEGER_URL, JaegerClient
from app.shared.schemas.workflow import WorkflowGraph


router = APIRouter(
    prefix="/api/workflows",
    tags=["Workflow Dependency Modeling"],
)

NOT_FOUND_RESPONSE = {404: {"description": "Requested workflow evidence was not found"}}
JAEGER_UNAVAILABLE_RESPONSE = {503: {"description": "Jaeger service is unavailable"}}
JAEGER_AND_NOT_FOUND_RESPONSES = {
    **NOT_FOUND_RESPONSE,
    **JAEGER_UNAVAILABLE_RESPONSE,
}
JAEGER_URL_DESCRIPTION = "Jaeger server base URL"


@router.get("/live/evidence", responses=JAEGER_AND_NOT_FOUND_RESPONSES)
def read_live_evidence(
    operation: str = Query(..., min_length=1),
    service_name: str = Query("frontend"),
    jaeger_url: str = Query(DEFAULT_JAEGER_URL),
    limit: int = Query(1000, ge=1, le=5000),
    lookback: str = Query("1h"),
) -> dict[str, Any]:
    try:
        return get_live_evidence(service_name, operation, jaeger_url, limit, lookback)
    except ConnectionError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.get(
    "/sample",
    response_model=WorkflowGraph,
    response_model_by_alias=True,
    summary="Get sample workflow graph",
)
def read_sample_workflow() -> WorkflowGraph:
    """Return the checkout workflow graph computed from sample Jaeger trace data."""
    return get_sample_workflow()


@router.get(
    "/benchmarks",
    summary="List all reference benchmark workflows",
)
def get_benchmark_workflows() -> list[dict[str, Any]]:
    """List all available reference benchmark workflows in the repository."""
    return list_benchmark_workflows()


@router.get(
    "/benchmark/{workflow_id}",
    response_model_by_alias=True,
    summary="Get workflow graph for a specific benchmark workflow",
    responses=NOT_FOUND_RESPONSE,
)
def read_benchmark_workflow(
    workflow_id: str,
    mandatory_threshold: float = Query(1.0, description="Mandatory frequency threshold"),
) -> WorkflowGraph:
    """Build and return a WorkflowGraph for a specific benchmark workflow ID."""
    try:
        return get_workflow_by_id(workflow_id, mandatory_threshold=mandatory_threshold)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.get(
    "/evaluation/accuracy",
    summary="Evaluate accuracy of workflow extraction against ground-truth reference",
    responses=NOT_FOUND_RESPONSE,
)
def evaluate_workflow_accuracy(
    workflow_id: str = Query("checkout", description="Benchmark workflow ID"),
    mandatory_threshold: float = Query(1.0, description="Mandatory threshold"),
) -> dict[str, Any]:
    """Compare extracted WorkflowGraph against reference definition and compute Precision, Recall, F1, and Jaccard Edge Accuracy."""
    try:
        workflow_def = get_workflow_definition(workflow_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err

    graph = get_workflow_by_id(workflow_id, mandatory_threshold=mandatory_threshold)
    result = evaluate_graph(graph, workflow_def)

    return {
        "workflowId": workflow_id,
        "mandatoryThreshold": mandatory_threshold,
        "serviceMetrics": {
            "precision": result.service_metrics.precision,
            "recall": result.service_metrics.recall,
            "f1": result.service_metrics.f1,
            "accuracy": result.service_metrics.accuracy,
            "truePositives": result.service_metrics.true_positives,
            "falsePositives": result.service_metrics.false_positives,
            "falseNegatives": result.service_metrics.false_negatives,
        },
        "edgeMetrics": {
            "precision": result.edge_metrics.precision,
            "recall": result.edge_metrics.recall,
            "f1": result.edge_metrics.f1,
            "accuracy": result.edge_metrics.accuracy,
            "truePositives": result.edge_metrics.true_positives,
            "falsePositives": result.edge_metrics.false_positives,
            "falseNegatives": result.edge_metrics.false_negatives,
        },
        "truePositiveServices": result.true_positive_services,
        "falsePositiveServices": result.false_positive_services,
        "falseNegativeServices": result.false_negative_services,
    }


@router.get(
    "/evaluation/sensitivity",
    summary="Run sensitivity analysis across threshold range",
    responses=NOT_FOUND_RESPONSE,
)
def get_sensitivity_analysis(
    workflow_id: str = Query("checkout", description="Benchmark workflow ID"),
) -> list[dict[str, Any]]:
    """Run sensitivity analysis varying threshold from 0.5 to 1.0 to generate thesis comparison curves."""
    try:
        steps = run_sensitivity_analysis(workflow_id)
        return [
            {
                "threshold": s.threshold,
                "precision": s.precision,
                "recall": s.recall,
                "f1": s.f1,
                "edgeAccuracy": s.edge_accuracy,
                "mandatoryServicesCount": s.mandatory_services_count,
                "optionalServicesCount": s.optional_services_count,
                "mandatoryServices": s.mandatory_services,
                "optionalServices": s.optional_services,
            }
            for s in steps
        ]
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err




@router.get(
    "/jaeger/status",
    summary="Check Jaeger server connectivity",
)
def check_jaeger_status(
    jaeger_url: str = Query(DEFAULT_JAEGER_URL, description=JAEGER_URL_DESCRIPTION),
) -> dict[str, Any]:
    """Check whether the Jaeger query server is online and reachable."""
    client = JaegerClient(base_url=jaeger_url)
    healthy = client.is_healthy()
    return {
        "healthy": healthy,
        "jaegerUrl": jaeger_url,
        "message": "Jaeger is reachable" if healthy else "Jaeger server unreachable",
    }


@router.post(
    "/jaeger/seed",
    summary="Seed demo traces into Jaeger OTLP receiver",
    responses=JAEGER_UNAVAILABLE_RESPONSE,
)
def seed_jaeger_traces(
    otlp_url: str = Query("http://localhost:4318", description="Jaeger OTLP HTTP receiver URL"),
) -> dict[str, Any]:
    """Push synthetic demo spans directly into Jaeger for testing and discovery."""
    client = JaegerClient()
    success = client.seed_demo_traces(otlp_url=otlp_url)
    if not success:
        raise HTTPException(
            status_code=503,
            detail=f"Could not seed demo traces to Jaeger OTLP receiver at {otlp_url}",
        )
    return {
        "success": True,
        "message": "Successfully seeded demo traces into Jaeger for service 'frontend'",
    }


@router.get(
    "/jaeger/services",
    summary="List all services recorded in Jaeger",
    responses=JAEGER_UNAVAILABLE_RESPONSE,
)
def list_jaeger_services(
    jaeger_url: str = Query(DEFAULT_JAEGER_URL, description=JAEGER_URL_DESCRIPTION),
) -> list[str]:
    """List all instrumented microservices that have reported traces to Jaeger."""
    client = JaegerClient(base_url=jaeger_url)
    try:
        return client.get_services()
    except ConnectionError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err



@router.get(
    "/discover",
    summary="Auto-discover all entry workflows from Jaeger traces",
    responses=JAEGER_UNAVAILABLE_RESPONSE,
)
def discover_workflows(
    service_name: str = Query("frontend", description="Ingress / entry microservice name"),
    jaeger_url: str = Query(DEFAULT_JAEGER_URL, description=JAEGER_URL_DESCRIPTION),
    limit: int = Query(100, description="Max traces to analyze"),
    lookback: str = Query("1h", description="Time lookback window"),
) -> list[dict[str, Any]]:
    """Scan Jaeger traces and auto-discover root transactions/workflows without manual input."""
    try:
        return discover_live_workflows(
            service_name=service_name,
            jaeger_url=jaeger_url,
            limit=limit,
            lookback=lookback,
        )
    except ConnectionError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err


@router.get(
    "/live",
    response_model_by_alias=True,
    summary="Fetch live traces and build WorkflowGraph",
    responses=JAEGER_AND_NOT_FOUND_RESPONSES,
)
def read_live_workflow(
    service_name: str = Query("frontend", description="Entry microservice name"),
    operation: str | None = Query(None, description="Optional root operation name filter"),
    jaeger_url: str = Query(DEFAULT_JAEGER_URL, description=JAEGER_URL_DESCRIPTION),
    criticality: float = Query(0.8, description="Business criticality rating (0.0 to 1.0)"),
    mandatory_threshold: float = Query(1.0, description="Mandatory frequency threshold (default 1.0 = 100%)"),
    limit: int = Query(100, description="Max traces to fetch"),
    lookback: str = Query("1h", description="Time lookback window"),
) -> WorkflowGraph:
    """Fetch live traces from Jaeger and run real-time dependency extraction."""
    try:
        return get_live_workflow(
            service_name=service_name,
            operation=operation,
            jaeger_url=jaeger_url,
            criticality=criticality,
            mandatory_threshold=mandatory_threshold,
            limit=limit,
            lookback=lookback,
        )
    except ConnectionError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
