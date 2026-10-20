"""Research-only routes: synthetic fixtures, benchmark experiments and chaos scenarios.

These handlers were part of the application's API until Phase 1 of the real-data-only work. They serve
sample/synthetic data that does not come from any connected cluster, so they are NOT mounted by
``app.main`` and are not shipped in the extension image. They live under ``tests/`` so the research
tests that exercise them over HTTP keep running unchanged, and so nobody can mistake their output for
live evidence. Do not import this module from application code.

The handler bodies below are the original ones, kept verbatim.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query, Response, status

# ---- Component 2 (deployment topology) --------------------------------------------------------------
from app.api.deployments import (
    DESC_K8S_NAMESPACE,
    DESC_TARGET_NAMESPACE,
    TAG_CHAOS,
    TAG_TOPOLOGY,
)
from app.engine.deployment_topology_modeling.service import (
    get_academic_accuracy_certificate,
    get_parametric_ensemble_markdown_report,
    get_sample_deployment,
    get_scenario_latex_table,
    run_chaos_benchmark_pipeline,
    run_parametric_ensemble_pipeline,
    run_single_chaos_scenario,
)
from app.shared.schemas.deployment import DeploymentGraph
from experiments.placement_drift.disruption_orchestrator import DisruptionOrchestrator
from experiments.placement_drift.disruption_reporter import DisruptionReporter
from experiments.placement_drift.drift_transition_matrix import DriftTransitionMatrix
from experiments.placement_drift.snapshot_repository import SnapshotRepository

TAG_BENCHMARKS = "6. Academic Benchmarks & Publications"
TAG_CHAOS = "4. Chaos Engineering (Disruption Scenarios)"

# ---- Component 1 (workflow dependency modelling) ----------------------------------------------------
from app.api.workflows import (  # noqa: E402
    DEFAULT_JAEGER_URL,
    JAEGER_UNAVAILABLE_RESPONSE,
    NOT_FOUND_RESPONSE,
    JaegerClient,
    _graph_evaluation_payload,
)
from app.engine.workflow_dependency_modeling.evaluator import run_sensitivity_analysis  # noqa: E402
from app.engine.workflow_dependency_modeling.execution_repository import ExecutionAlreadyExistsError  # noqa: E402
from app.engine.workflow_dependency_modeling.service import (  # noqa: E402
    evaluate_workflow_by_id,
    get_sample_workflow,
    get_workflow_by_id,
    list_benchmark_workflows,
    run_benchmark_batch,
)
from app.shared.schemas.workflow import WorkflowGraph  # noqa: E402

# ---- Components 3 and 4 -----------------------------------------------------------------------------
from app.engine.failure_impact_prediction.service import (  # noqa: E402
    get_demo_deployment,
    get_sample_failure_impact,
    predict_demo_failure_impact,
)
from app.engine.recommendation_evaluation_validation.service import (  # noqa: E402
    get_sample_recommendation,
    get_sample_validation,
)
from app.shared.schemas.failure_impact import FailureImpactReport  # noqa: E402
from app.shared.schemas.recommendation import RecommendationReport  # noqa: E402
from app.shared.schemas.validation import ValidationReport  # noqa: E402

deployments_router = APIRouter(prefix="/api/deployments")
workflows_router = APIRouter(prefix="/api/workflows", tags=["Research: workflow fixtures"])
failure_impacts_router = APIRouter(prefix="/api/failure-impacts", tags=["Research: failure impact fixtures"])
recommendations_router = APIRouter(prefix="/api/recommendations", tags=["Research: recommendation fixtures"])
validations_router = APIRouter(prefix="/api/validations", tags=["Research: validation fixtures"])

# ---- deployments --------------------------------------------------------------------------

@deployments_router.get(
    "/sample",
    response_model_by_alias=True,
    tags=[TAG_TOPOLOGY],
    summary="[Phase 1] Retrieve baseline static sample deployment graph",
)
def read_sample_deployment() -> DeploymentGraph:
    """Returns static golden reference topology model for offline testing."""
    return get_sample_deployment()


@deployments_router.get(
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


@deployments_router.get(
    "/chaos/scenarios/{scenario_id}",
    tags=[TAG_CHAOS],
    summary="[Phase 4] Inspect scenario specification, parameters, and rollback design",
    responses={404: {"description": "Scenario not found"}},
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


@deployments_router.post(
    "/chaos/run/{scenario_id}",
    tags=[TAG_CHAOS],
    summary="[Phase 4] Execute a single designated disruption scenario by ID",
    responses={404: {"description": "Scenario not found"}},
)
def run_chaos_scenario(
    scenario_id: str,
    namespace: str = Query("default", description=DESC_TARGET_NAMESPACE),
) -> Dict[str, Any]:
    """Injects a specific disruption fault (e.g. node drain, pod crash) and calculates placement drift."""
    result = run_single_chaos_scenario(scenario_id=scenario_id, namespace=namespace)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")
    return result.model_dump()


@deployments_router.get(
    "/chaos/matrix/{scenario_id}",
    tags=[TAG_CHAOS],
    summary="[Phase 4] Get Markov pod migration transition matrix for a scenario",
    responses={
        400: {"description": "Snapshot data missing for scenario"},
        404: {"description": "Scenario not found"},
    },
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


@deployments_router.get(
    "/chaos/benchmarks",
    tags=[TAG_BENCHMARKS],
    summary="[Academic Defense] Run all 8 disruption scenarios and compile benchmark metrics",
)
def get_chaos_benchmarks(
    namespace: str = Query("default", description=f"{DESC_TARGET_NAMESPACE} for chaos simulation"),
) -> Dict[str, Any]:
    """Executes all 8 Section 10 disruption scenarios and returns D_rate, delta_E_rate, and transitions."""
    return run_chaos_benchmark_pipeline(namespace=namespace)


@deployments_router.get(
    "/benchmarks/accuracy",
    tags=[TAG_BENCHMARKS],
    summary="[Academic Defense] Model Accuracy Certificate (>=98% Mapping, >=95% Concentration)",
)
def get_accuracy_certificate() -> Dict[str, Any]:
    """Evaluates ground-truth accuracy metrics required by the viva evaluation panel."""
    cert = get_academic_accuracy_certificate()
    return cert.model_dump() if hasattr(cert, "model_dump") else dict(cert)


@deployments_router.get(
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


@deployments_router.get(
    "/benchmarks/report/json",
    tags=[TAG_BENCHMARKS],
    summary="[Academic Defense] Export full publication-grade JSON benchmark dataset",
)
def get_json_benchmark_report(
    namespace: str = Query("default", description=DESC_TARGET_NAMESPACE),
) -> Dict[str, Any]:
    """Returns serialized JSON benchmark dataset with aggregate metrics and per-scenario telemetry."""
    return run_chaos_benchmark_pipeline(namespace=namespace)


@deployments_router.get(
    "/benchmarks/latex/{scenario_id}",
    tags=[TAG_BENCHMARKS],
    summary="[Academic Defense] Export publication-ready LaTeX transition matrix table",
    responses={404: {"description": "Scenario not found or snapshot missing"}},
)
def get_latex_transition_table(
    scenario_id: str,
) -> Dict[str, Any]:
    """Returns LaTeX table code snippet for the Markov transition grid T[i, j] of a scenario."""
    res = get_scenario_latex_table(scenario_id=scenario_id)
    if res is None:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found or snapshot missing.")
    return res


@deployments_router.get(
    "/benchmarks/ensemble",
    tags=[TAG_BENCHMARKS],
    summary="[Academic Defense] Multi-Service Parametric Ensemble Evaluation (Mean, Std Dev, 95% CI)",
)
def get_parametric_ensemble_dataset() -> Dict[str, Any]:
    """Executes a 12-trial multi-workload parametric sweep and returns statistical ensemble metrics."""
    return run_parametric_ensemble_pipeline()


@deployments_router.get(
    "/benchmarks/ensemble/markdown",
    tags=[TAG_BENCHMARKS],
    summary="[Academic Defense] Export publication-grade Markdown report for Multi-Service Ensemble",
)
def get_parametric_ensemble_markdown() -> Response:
    """Returns formatted Markdown report with statistical summary and sensitivity tables for publication."""
    md_content = get_parametric_ensemble_markdown_report()
    return Response(content=md_content, media_type="text/markdown")


# ---- workflows ----------------------------------------------------------------------------

@workflows_router.post(
    "/execution-batches/benchmarks/{workflow_id}",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Benchmark workflow or evidence was not found"},
        409: {"description": "Execution ID already exists"},
        422: {"description": "Execution registration is invalid"},
    },
)
def execute_benchmark_batch(
    workflow_id: str,
    actor: str = Header(..., alias="X-Actor", min_length=1),
    batch_id: str | None = Query(None, min_length=1),
) -> list[dict[str, Any]]:
    """Evaluate each separated fixture trace as an immutable execution record."""
    try:
        return [
            outcome.to_mapping()
            for outcome in run_benchmark_batch(
                workflow_id,
                actor=actor,
                batch_id=batch_id,
            )
        ]
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ExecutionAlreadyExistsError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@workflows_router.get(
    "/sample",
    response_model=WorkflowGraph,
    response_model_by_alias=True,
    summary="Get sample workflow graph",
)
def read_sample_workflow() -> WorkflowGraph:
    """Return the checkout workflow graph computed from sample Jaeger trace data."""
    return get_sample_workflow()


@workflows_router.get(
    "/benchmarks",
    summary="List separated synthetic workflow fixtures",
)
def get_benchmark_workflows() -> list[dict[str, Any]]:
    """List scenario/evidence fixtures and whether a reference is available."""
    return list_benchmark_workflows()


@workflows_router.get(
    "/benchmark/{workflow_id}",
    response_model_by_alias=True,
    summary="Get workflow graph for a specific benchmark workflow",
    responses=NOT_FOUND_RESPONSE,
)
def read_benchmark_workflow(
    workflow_id: str,
    mandatory_threshold: float = Query(
        1.0, ge=0.0, le=1.0, description="Mandatory-candidate frequency threshold"
    ),
) -> WorkflowGraph:
    """Build and return a WorkflowGraph for a specific benchmark workflow ID."""
    try:
        return get_workflow_by_id(workflow_id, mandatory_threshold=mandatory_threshold)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@workflows_router.get(
    "/evaluation/accuracy",
    summary="Compare a fixture prediction with its separate synthetic reference",
    responses=NOT_FOUND_RESPONSE,
)
def evaluate_workflow_accuracy(
    workflow_id: str = Query("checkout", description="Benchmark workflow ID"),
    mandatory_threshold: float = Query(
        1.0, ge=0.0, le=1.0, description="Mandatory-candidate frequency threshold"
    ),
) -> dict[str, Any]:
    """Evaluate only after prediction, using the separate reference store."""
    try:
        result = evaluate_workflow_by_id(
            workflow_id, mandatory_threshold=mandatory_threshold
        )
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err

    return _graph_evaluation_payload(
        result,
        mandatory_threshold=mandatory_threshold,
        graph_processing_time_ms=result.graph_processing_time_ms,
    )


@workflows_router.get(
    "/evaluation/sensitivity",
    summary="Run sensitivity analysis across threshold range",
    deprecated=True,
    responses=NOT_FOUND_RESPONSE,
)
def get_sensitivity_analysis(
    workflow_id: str = Query("checkout", description="Benchmark workflow ID"),
) -> list[dict[str, Any]]:
    """Run mandatory-aware synthetic fixture sensitivity for compatibility."""
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


@workflows_router.post(
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


# ---- failure impacts ----------------------------------------------------------------------

@failure_impacts_router.get(
    "/sample",
    response_model=FailureImpactReport,
    response_model_by_alias=True,
)
def read_sample_failure_impact() -> FailureImpactReport:
    return get_sample_failure_impact()


@failure_impacts_router.get(
    "/demo-deployment",
    response_model=DeploymentGraph,
    response_model_by_alias=True,
)
def read_demo_deployment() -> DeploymentGraph:
    """Return disclosed synthetic placements completing current upstream samples."""

    return get_demo_deployment()


@failure_impacts_router.get(
    "/simulate",
    response_model=FailureImpactReport,
    response_model_by_alias=True,
    responses={422: {"description": "The selected worker node is unknown"}},
)
def simulate_demo_failure(
    failed_node: str = Query(
        "node-1",
        min_length=1,
        description="Worker node to remove virtually from the demo topology",
    ),
) -> FailureImpactReport:
    """Run Component 3's deterministic engine over contract-compatible fixtures."""

    try:
        return predict_demo_failure_impact(failed_node)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


# ---- recommendations / validations --------------------------------------------------------

@recommendations_router.get(
    "/sample",
    response_model=RecommendationReport,
    response_model_by_alias=True,
)
def read_sample_recommendation() -> RecommendationReport:
    return get_sample_recommendation()


@validations_router.get(
    "/sample",
    response_model=ValidationReport,
    response_model_by_alias=True,
)
def read_sample_validation() -> ValidationReport:
    return get_sample_validation()
