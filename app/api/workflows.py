from typing import Any
import httpx
from fastapi import APIRouter, Body, Header, HTTPException, Query, Response, status

from app.engine.workflow_dependency_modeling.evaluator import (
    run_sensitivity_analysis,
)
from app.engine.workflow_dependency_modeling.service import (
    approve_experiment_campaign,
    assess_experiment_campaign,
    approve_dependency_assessment,
    baseline_readiness,
    intervention_status,
    record_intervention_event,
    start_workflow_analysis,
    create_scenario,
    create_dependency_assessment,
    create_dependency_classification_evaluation,
    create_dependency_classification_reference,
    create_experiment_campaign,
    discover_live_workflows,
    discover_analysis_workflows,
    evaluate_workflow_by_id,
    get_live_workflow,
    get_workflow_analysis,
    get_live_evidence,
    get_execution,
    get_experiment_campaign,
    get_experiment_campaign_assessment,
    get_dependency_assessment,
    get_dependency_classification_evaluation,
    get_dependency_classification_reference,
    get_sample_workflow,
    get_workflow_by_id,
    get_versioned_scenario,
    get_validated_workflow_graph,
    list_benchmark_workflows,
    list_executions,
    list_experiment_campaigns,
    list_dependency_assessments,
    list_dependency_classification_evaluations,
    list_dependency_classification_references,
    list_versioned_scenarios,
    list_workflow_analyses,
    register_jaeger_execution,
    run_dependency_repeatability_analysis,
    run_dependency_sensitivity_analysis,
    run_benchmark_batch,
    run_campaign_baseline_trial,
    run_campaign_intervention_trial,
    scenario_etag,
    update_scenario,
)
from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    AnalysisSessionConflictError,
    AnalysisSessionNotFoundError,
)
from app.engine.workflow_dependency_modeling.classification_evaluation_repository import (
    ClassificationArtifactConflictError,
    ClassificationArtifactNotFoundError,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_repository import (
    DependencyAssessmentAlreadyExistsError,
    DependencyAssessmentNotFoundError,
)
from app.engine.workflow_dependency_modeling.execution_repository import (
    ExecutionAlreadyExistsError,
    ExecutionNotFoundError,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_repository import (
    ExperimentCampaignConflictError,
    ExperimentCampaignNotFoundError,
)
from app.engine.workflow_dependency_modeling.intervention_event_repository import (
    InterventionEventConflictError,
)
from app.engine.workflow_dependency_modeling.scenario_repository import (
    ScenarioAlreadyExistsError,
    ScenarioNotFoundError,
    ScenarioVersionConflictError,
)
from app.engine.workflow_dependency_modeling.scenario_service import parse_scenario_etag
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
SCENARIO_NOT_FOUND_RESPONSE = {404: {"description": "Scenario or version not found"}}
SCENARIO_CONFLICT_RESPONSE = {409: {"description": "Scenario already exists"}}
SCENARIO_PRECONDITION_RESPONSE = {412: {"description": "Scenario version changed"}}
SCENARIO_VALIDATION_RESPONSE = {422: {"description": "Scenario definition is invalid"}}
EXECUTION_NOT_FOUND_RESPONSE = {404: {"description": "Execution was not found"}}


def _scenario_payload(response: Response, scenario) -> dict[str, Any]:
    response.headers["ETag"] = scenario_etag(scenario)
    return scenario.to_mapping()


@router.post(
    "/scenarios",
    status_code=status.HTTP_201_CREATED,
    responses={**SCENARIO_CONFLICT_RESPONSE, **SCENARIO_VALIDATION_RESPONSE},
)
def create_workflow_scenario(
    response: Response,
    payload: dict[str, Any] = Body(...),
    actor: str = Header(..., alias="X-Actor", min_length=1),
) -> dict[str, Any]:
    """Create version 1 from a manual or discovery-assisted definition."""
    try:
        scenario = create_scenario(payload, actor=actor)
        return _scenario_payload(response, scenario)
    except ScenarioAlreadyExistsError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.get("/scenarios")
def read_workflow_scenarios() -> list[dict[str, Any]]:
    """List only the latest immutable version of each scenario."""
    return [scenario.to_mapping() for scenario in list_versioned_scenarios()]


@router.get(
    "/scenarios/{workflow_id}",
    responses=SCENARIO_NOT_FOUND_RESPONSE,
)
def read_workflow_scenario(
    workflow_id: str,
    response: Response,
    version: int | None = Query(None, ge=1),
) -> dict[str, Any]:
    """Read the latest or an explicitly requested immutable version."""
    try:
        scenario = get_versioned_scenario(workflow_id, version)
        return _scenario_payload(response, scenario)
    except ScenarioNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.patch(
    "/scenarios/{workflow_id}",
    responses={
        **SCENARIO_NOT_FOUND_RESPONSE,
        **SCENARIO_PRECONDITION_RESPONSE,
        **SCENARIO_VALIDATION_RESPONSE,
    },
)
def revise_workflow_scenario(
    workflow_id: str,
    response: Response,
    payload: dict[str, Any] = Body(...),
    if_match: str = Header(..., alias="If-Match"),
    actor: str = Header(..., alias="X-Actor", min_length=1),
) -> dict[str, Any]:
    """Create the next version only when the caller holds the latest ETag."""
    try:
        expected_version = parse_scenario_etag(if_match, workflow_id)
        scenario = update_scenario(
            workflow_id,
            payload,
            expected_version=expected_version,
            actor=actor,
        )
        return _scenario_payload(response, scenario)
    except ScenarioNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ScenarioVersionConflictError as err:
        raise HTTPException(status_code=412, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.post(
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


@router.post(
    "/executions/jaeger",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Scenario or version was not found"},
        409: {"description": "Execution ID already exists"},
        422: {"description": "Execution registration is invalid"},
        503: {"description": "Jaeger service is unavailable"},
    },
)
def register_workflow_execution(
    payload: dict[str, Any] = Body(...),
    actor: str = Header(..., alias="X-Actor", min_length=1),
    jaeger_url: str = Query(DEFAULT_JAEGER_URL, description=JAEGER_URL_DESCRIPTION),
    lookback: str = Query("1h"),
) -> dict[str, Any]:
    """Register an external invocation and correlate its exact trace ID."""
    try:
        return register_jaeger_execution(
            payload,
            actor=actor,
            jaeger_url=jaeger_url,
            lookback=lookback,
        ).to_mapping()
    except ScenarioNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ExecutionAlreadyExistsError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ConnectionError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.get("/executions")
def read_workflow_executions(
    workflow_id: str | None = Query(None),
    batch_id: str | None = Query(None),
) -> list[dict[str, Any]]:
    """List execution outcomes, optionally filtered by workflow or batch."""
    return [
        outcome.to_mapping()
        for outcome in list_executions(workflow_id=workflow_id, batch_id=batch_id)
    ]


@router.get(
    "/executions/{execution_id}",
    responses=EXECUTION_NOT_FOUND_RESPONSE,
)
def read_workflow_execution(execution_id: str) -> dict[str, Any]:
    """Read one immutable execution outcome with its evidence provenance."""
    try:
        return get_execution(execution_id).to_mapping()
    except ExecutionNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post(
    "/dependency-assessments",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Scenario or execution batch was not found"},
        409: {"description": "Assessment already exists"},
        422: {"description": "Assessment evidence or policy is invalid"},
    },
)
def create_workflow_dependency_assessment(
    payload: dict[str, Any] = Body(...),
    actor: str = Header(..., alias="X-Actor", min_length=1),
) -> dict[str, Any]:
    """Compare exact-correlated baseline and intervention execution cohorts."""
    try:
        return create_dependency_assessment(payload, actor=actor).to_mapping()
    except (ScenarioNotFoundError, ExecutionNotFoundError) as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except DependencyAssessmentAlreadyExistsError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.get("/dependency-assessments")
def read_workflow_dependency_assessments(
    workflow_id: str | None = Query(None),
) -> list[dict[str, Any]]:
    return [
        assessment.to_mapping()
        for assessment in list_dependency_assessments(workflow_id=workflow_id)
    ]


@router.get(
    "/dependency-assessments/{assessment_id}",
    responses={404: {"description": "Dependency assessment was not found"}},
)
def read_workflow_dependency_assessment(assessment_id: str) -> dict[str, Any]:
    try:
        return get_dependency_assessment(assessment_id).to_mapping()
    except DependencyAssessmentNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post(
    "/dependency-assessments/{assessment_id}/approve",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Dependency assessment was not found"},
        409: {"description": "Assessment is already approved"},
        422: {"description": "Assessment cannot pass the approval gate"},
    },
)
def approve_workflow_dependency_assessment(
    assessment_id: str,
    payload: dict[str, Any] = Body(...),
    actor: str = Header(..., alias="X-Actor", min_length=1),
) -> dict[str, Any]:
    try:
        return approve_dependency_assessment(
            assessment_id,
            actor=actor,
            rationale=payload.get("rationale"),
        ).to_mapping()
    except DependencyAssessmentNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except DependencyAssessmentAlreadyExistsError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.get(
    "/{workflow_id}/validated-graph",
    response_model=WorkflowGraph,
    response_model_by_alias=True,
    responses={404: {"description": "No approved dependency assessment was found"}},
)
def read_validated_workflow_graph(workflow_id: str) -> WorkflowGraph:
    try:
        return get_validated_workflow_graph(workflow_id)
    except DependencyAssessmentNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post(
    "/dependency-references",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Scenario was not found"},
        409: {"description": "Reference version changed"},
        422: {"description": "Reference labels are invalid"},
    },
)
def create_workflow_dependency_reference(
    payload: dict[str, Any] = Body(...),
    actor: str = Header(..., alias="X-Actor", min_length=1),
    expected_version: int = Query(0, ge=0),
) -> dict[str, Any]:
    try:
        return create_dependency_classification_reference(
            payload,
            actor=actor,
            expected_version=expected_version,
        ).to_mapping()
    except ScenarioNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ClassificationArtifactConflictError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.post(
    "/analysis-sessions",
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"description": "Analysis session already exists"},
        422: {"description": "Connection or operation input is invalid"},
        503: {"description": "Jaeger service is unavailable"},
    },
)
def create_workflow_analysis_session(
    payload: dict[str, Any] = Body(...),
    actor: str = Header(..., alias="X-Actor", min_length=1),
) -> dict[str, Any]:
    """Inspect one real trace operation without claiming dependency validation."""
    try:
        return start_workflow_analysis(payload, actor=actor).to_mapping()
    except AnalysisSessionConflictError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ConnectionError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.get("/analysis-sessions")
def read_workflow_analysis_sessions() -> list[dict[str, Any]]:
    return [analysis.to_mapping() for analysis in list_workflow_analyses()]


@router.post(
    "/analysis-sessions/discover",
    responses={
        422: {"description": "Connection input is invalid"},
        503: {"description": "Jaeger service is unavailable"},
    },
)
def discover_workflow_analysis_operations(
    payload: dict[str, Any] = Body(...),
) -> list[dict[str, Any]]:
    try:
        return discover_analysis_workflows(payload)
    except ConnectionError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.get(
    "/analysis-sessions/{session_id}",
    responses={404: {"description": "Analysis session was not found"}},
)
def read_workflow_analysis_session(session_id: str) -> dict[str, Any]:
    try:
        return get_workflow_analysis(session_id).to_mapping()
    except AnalysisSessionNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post(
    "/experiment-campaigns",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Analysis session was not found"},
        409: {"description": "Experiment campaign already exists"},
        422: {"description": "Experiment plan is invalid or unsafe"},
    },
)
def create_workflow_experiment_campaign(
    payload: dict[str, Any] = Body(...),
    actor: str = Header(..., alias="X-Actor", min_length=1),
) -> dict[str, Any]:
    """Create a safe Online Boutique checkout experiment from observed evidence."""
    try:
        return create_experiment_campaign(payload, actor=actor).to_mapping()
    except AnalysisSessionNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ExperimentCampaignConflictError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.get("/experiment-campaigns")
def read_workflow_experiment_campaigns() -> list[dict[str, Any]]:
    return [campaign.to_mapping() for campaign in list_experiment_campaigns()]


@router.get(
    "/experiment-campaigns/{campaign_id}",
    responses={404: {"description": "Experiment campaign was not found"}},
)
def read_workflow_experiment_campaign(campaign_id: str) -> dict[str, Any]:
    try:
        return get_experiment_campaign(campaign_id).to_mapping()
    except ExperimentCampaignNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post(
    "/experiment-campaigns/{campaign_id}/baseline-runs",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Experiment campaign or analysis session was not found"},
        409: {"description": "Execution evidence already exists"},
        422: {"description": "Baseline run is invalid or no longer allowed"},
        503: {"description": "Online Boutique or Jaeger is unavailable"},
    },
)
def execute_workflow_campaign_baseline(
    campaign_id: str,
    payload: dict[str, Any] = Body(...),
    actor: str = Header(..., alias="X-Actor", min_length=1),
) -> dict[str, Any]:
    frontend_url = payload.get("frontendUrl")
    try:
        return run_campaign_baseline_trial(
            campaign_id,
            frontend_url,
            actor=actor,
        ).to_mapping()
    except (ExperimentCampaignNotFoundError, AnalysisSessionNotFoundError) as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ExecutionAlreadyExistsError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except (ConnectionError, httpx.HTTPError) as err:
        raise HTTPException(status_code=503, detail="Experiment evidence source is unavailable") from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.get(
    "/experiment-campaigns/{campaign_id}/baseline-readiness",
    responses={404: {"description": "Experiment campaign was not found"}},
)
def read_workflow_campaign_baseline_readiness(campaign_id: str) -> dict[str, Any]:
    try:
        return baseline_readiness(get_experiment_campaign(campaign_id))
    except ExperimentCampaignNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post(
    "/experiment-campaigns/{campaign_id}/intervention-events",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Experiment campaign was not found"},
        409: {"description": "Intervention event already exists"},
        422: {"description": "Intervention transition or evidence is invalid"},
    },
)
def create_workflow_campaign_intervention_event(
    campaign_id: str,
    payload: dict[str, Any] = Body(...),
    actor: str = Header(..., alias="X-Actor", min_length=1),
) -> dict[str, Any]:
    try:
        return record_intervention_event(
            campaign_id,
            payload,
            actor=actor,
        ).to_mapping()
    except ExperimentCampaignNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except InterventionEventConflictError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.get(
    "/experiment-campaigns/{campaign_id}/intervention-status",
    responses={404: {"description": "Experiment campaign was not found"}},
)
def read_workflow_campaign_intervention_status(campaign_id: str) -> dict[str, Any]:
    try:
        return intervention_status(campaign_id)
    except ExperimentCampaignNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post(
    "/experiment-campaigns/{campaign_id}/interventions/{target_service}/runs",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Experiment campaign or analysis session was not found"},
        409: {"description": "Execution evidence already exists"},
        422: {"description": "Intervention run is invalid or not currently allowed"},
        503: {"description": "Online Boutique or Jaeger is unavailable"},
    },
)
def execute_workflow_campaign_intervention(
    campaign_id: str,
    target_service: str,
    payload: dict[str, Any] = Body(...),
    actor: str = Header(..., alias="X-Actor", min_length=1),
) -> dict[str, Any]:
    try:
        return run_campaign_intervention_trial(
            campaign_id,
            target_service,
            payload.get("frontendUrl"),
            actor=actor,
        ).to_mapping()
    except (ExperimentCampaignNotFoundError, AnalysisSessionNotFoundError) as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ExecutionAlreadyExistsError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except (ConnectionError, httpx.HTTPError) as err:
        raise HTTPException(status_code=503, detail="Experiment evidence source is unavailable") from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.post(
    "/experiment-campaigns/{campaign_id}/assessment",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Experiment campaign or analysis session was not found"},
        409: {"description": "Campaign assessment already exists"},
        422: {"description": "Campaign evidence is incomplete or inconsistent"},
    },
)
def create_workflow_campaign_assessment(
    campaign_id: str,
    actor: str = Header(..., alias="X-Actor", min_length=1),
) -> dict[str, Any]:
    """Classify a completed campaign without asking for internal evidence IDs."""
    try:
        return assess_experiment_campaign(campaign_id, actor=actor).to_mapping()
    except (ExperimentCampaignNotFoundError, AnalysisSessionNotFoundError) as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except DependencyAssessmentAlreadyExistsError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.get(
    "/experiment-campaigns/{campaign_id}/assessment",
    responses={404: {"description": "Campaign or campaign assessment was not found"}},
)
def read_workflow_campaign_assessment(campaign_id: str) -> dict[str, Any]:
    try:
        return get_experiment_campaign_assessment(campaign_id).to_mapping()
    except (ExperimentCampaignNotFoundError, DependencyAssessmentNotFoundError) as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post(
    "/experiment-campaigns/{campaign_id}/approval",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Campaign or campaign assessment was not found"},
        409: {"description": "Campaign assessment is already approved"},
        422: {"description": "Approval is unconfirmed or evidence is unresolved"},
    },
)
def approve_workflow_campaign(
    campaign_id: str,
    payload: dict[str, Any] = Body(...),
    actor: str = Header(..., alias="X-Actor", min_length=1),
) -> dict[str, Any]:
    """Approve reviewed classifications and publish the Component 3 graph."""
    try:
        return approve_experiment_campaign(
            campaign_id,
            actor=actor,
            rationale=payload.get("rationale"),
            approval_confirmed=payload.get("approvalConfirmed"),
        ).to_mapping()
    except (ExperimentCampaignNotFoundError, DependencyAssessmentNotFoundError) as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except DependencyAssessmentAlreadyExistsError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.get("/dependency-references")
def read_workflow_dependency_references() -> list[dict[str, Any]]:
    return [
        reference.to_mapping()
        for reference in list_dependency_classification_references()
    ]


@router.get(
    "/dependency-references/{workflow_id}",
    responses={404: {"description": "Dependency reference was not found"}},
)
def read_workflow_dependency_reference(
    workflow_id: str,
    version: int | None = Query(None, ge=1),
) -> dict[str, Any]:
    try:
        return get_dependency_classification_reference(
            workflow_id, version
        ).to_mapping()
    except ClassificationArtifactNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post(
    "/dependency-evaluations",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Assessment or reference was not found"},
        409: {"description": "Evaluation already exists"},
        422: {"description": "Evaluation inputs are incompatible"},
    },
)
def create_workflow_dependency_evaluation(
    payload: dict[str, Any] = Body(...),
    actor: str = Header(..., alias="X-Actor", min_length=1),
) -> dict[str, Any]:
    try:
        return create_dependency_classification_evaluation(
            payload, actor=actor
        ).to_mapping()
    except (DependencyAssessmentNotFoundError, ClassificationArtifactNotFoundError) as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ClassificationArtifactConflictError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.get("/dependency-evaluations")
def read_workflow_dependency_evaluations(
    workflow_id: str | None = Query(None),
) -> list[dict[str, Any]]:
    return [
        evaluation.to_mapping()
        for evaluation in list_dependency_classification_evaluations(workflow_id)
    ]


@router.get(
    "/dependency-evaluations/{evaluation_id}",
    responses={404: {"description": "Classification evaluation was not found"}},
)
def read_workflow_dependency_evaluation(evaluation_id: str) -> dict[str, Any]:
    try:
        return get_dependency_classification_evaluation(evaluation_id).to_mapping()
    except ClassificationArtifactNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post(
    "/dependency-experiments/sensitivity",
    responses={
        404: {"description": "Dependency assessment was not found"},
        422: {"description": "Sensitivity experiment is invalid"},
    },
)
def run_workflow_dependency_sensitivity(
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    try:
        return run_dependency_sensitivity_analysis(payload).to_mapping()
    except DependencyAssessmentNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.post(
    "/dependency-experiments/repeatability",
    responses={
        404: {"description": "Dependency assessment was not found"},
        422: {"description": "Repeatability experiment is invalid"},
    },
)
def run_workflow_dependency_repeatability(
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    try:
        return run_dependency_repeatability_analysis(payload).to_mapping()
    except DependencyAssessmentNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


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
    summary="List separated synthetic workflow fixtures",
)
def get_benchmark_workflows() -> list[dict[str, Any]]:
    """List scenario/evidence fixtures and whether a reference is available."""
    return list_benchmark_workflows()


@router.get(
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


@router.get(
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
    limit: int = Query(100, ge=1, le=5000, description="Max traces to analyze"),
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
    deprecated=True,
    response_model_by_alias=True,
    summary="Fetch live traces and build WorkflowGraph",
    responses=JAEGER_AND_NOT_FOUND_RESPONSES,
)
def read_live_workflow(
    service_name: str = Query("frontend", description="Entry microservice name"),
    operation: str | None = Query(None, description="Optional root operation name filter"),
    jaeger_url: str = Query(DEFAULT_JAEGER_URL, description=JAEGER_URL_DESCRIPTION),
    criticality: float = Query(
        0.8, ge=0.0, le=1.0, description="Business criticality rating"
    ),
    mandatory_threshold: float = Query(
        1.0, ge=0.0, le=1.0,
        description="Mandatory-candidate frequency threshold",
    ),
    limit: int = Query(100, ge=1, le=5000, description="Max traces to fetch"),
    lookback: str = Query("1h", description="Time lookback window"),
) -> WorkflowGraph:
    """Legacy adapter returning a candidate graph; it is not a validated export."""
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
