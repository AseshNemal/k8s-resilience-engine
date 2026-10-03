"""Phase 4 causal-evidence classification and approval tests."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    AssessmentPolicy,
    InterventionCohort,
    ValidatedDependencyType,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_repository import (
    DependencyAssessmentNotApprovableError,
    InMemoryDependencyAssessmentRepository,
    SqlAlchemyDependencyAssessmentRepository,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_service import (
    approve_dependency_assessment,
    create_dependency_assessment,
)
from app.engine.workflow_dependency_modeling.dependency_assessor import (
    assess_service_dependency,
)
from app.engine.workflow_dependency_modeling.execution_models import (
    CorrelationStatus,
    ExecutionOutcome,
    ExecutionOutcomeStatus,
    ExecutionRegistration,
)
from app.engine.workflow_dependency_modeling.execution_repository import (
    InMemoryExecutionRepository,
)
from app.engine.workflow_dependency_modeling.models import WorkflowScenario
from app.engine.workflow_dependency_modeling.scenario_repository import (
    InMemoryScenarioRepository,
)
from app.main import app
from app.shared.persistence.models import Base


NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def _outcome(execution_id: str, batch_id: str, succeeded: bool) -> ExecutionOutcome:
    registration = ExecutionRegistration.from_mapping(
        {
            "executionId": execution_id,
            "batchId": batch_id,
            "workflowId": "checkout",
            "scenarioVersion": 1,
            "invocationStatus": "completed",
            "source": "controlled_experiment",
            "evidenceLocator": "jaeger://trace",
            "environmentId": "test-cluster-a",
            "expectedTraceId": f"trace:{execution_id}",
            "startedAt": "2026-10-03T00:00:00Z",
            "finishedAt": "2026-10-03T00:00:01Z",
            "registeredBy": "experiment-runner",
        }
    )
    return ExecutionOutcome(
        registration=registration,
        status=(
            ExecutionOutcomeStatus.SUCCEEDED
            if succeeded
            else ExecutionOutcomeStatus.FAILED
        ),
        correlation_status=CorrelationStatus.MATCHED,
        recorded_at=NOW,
        matched_trace_id=registration.expected_trace_id,
        evidence_id=f"trace:{registration.expected_trace_id}",
    )


def _cohort(service: str, batch_id: str) -> InterventionCohort:
    return InterventionCohort.from_mapping(
        {
            "targetService": service,
            "interventionType": "service_unavailable",
            "batchIds": [batch_id],
            "interventionConfirmed": True,
            "confirmationEvidence": f"chaos-run:{service}",
        }
    )


def _scenario() -> WorkflowScenario:
    return WorkflowScenario.from_mapping(
        {
            "workflowId": "checkout",
            "description": "Checkout",
            "criticality": 0.9,
            "entryService": "frontend",
            "successCondition": {
                "protocol": "http",
                "terminalService": "paymentservice",
                "operationName": "POST /charge",
                "httpStatusCodes": [200],
            },
        }
    )


def _assessment_payload() -> dict:
    return {
        "assessmentId": "assessment:checkout:1",
        "workflowId": "checkout",
        "scenarioVersion": 1,
        "environmentId": "test-cluster-a",
        "source": "controlled_experiment",
        "candidateGraph": {
            "workflowId": "checkout",
            "criticality": 0.9,
            "mandatoryServices": ["paymentservice", "inventoryservice"],
            "optionalServices": ["emailservice"],
            "edges": [
                {"source": "inventoryservice", "target": "paymentservice"},
                {"source": "paymentservice", "target": "emailservice"},
            ],
        },
        "baselineBatchIds": ["batch:baseline"],
        "interventions": [
            _cohort("paymentservice", "batch:payment").to_mapping(),
            _cohort("inventoryservice", "batch:inventory").to_mapping(),
            _cohort("emailservice", "batch:email").to_mapping(),
        ],
        "policy": AssessmentPolicy().to_mapping(),
    }


def _repositories() -> tuple[
    InMemoryScenarioRepository,
    InMemoryExecutionRepository,
    InMemoryDependencyAssessmentRepository,
]:
    scenarios = InMemoryScenarioRepository()
    scenarios.create(_scenario())
    executions = InMemoryExecutionRepository()
    batches = {
        "batch:baseline": [True, True, True],
        "batch:payment": [False, False, False],
        "batch:inventory": [True, True, False],
        "batch:email": [True, True, True],
    }
    for batch_id, results in batches.items():
        for index, succeeded in enumerate(results, start=1):
            executions.create(
                _outcome(f"{batch_id}:{index}", batch_id, succeeded)
            )
    return scenarios, executions, InMemoryDependencyAssessmentRepository()


def test_classifies_mandatory_optional_and_supporting_from_outcomes() -> None:
    baseline = [_outcome(f"base:{index}", "base", True) for index in range(3)]
    cases = [
        ("payment", [False, False, False], ValidatedDependencyType.MANDATORY),
        ("email", [True, True, True], ValidatedDependencyType.OPTIONAL),
        ("inventory", [True, True, False], ValidatedDependencyType.SUPPORTING),
    ]
    for service, results, expected in cases:
        intervention = [
            _outcome(f"{service}:{index}", service, result)
            for index, result in enumerate(results)
        ]
        assessed = assess_service_dependency(
            service,
            baseline,
            _cohort(service, service),
            intervention,
            AssessmentPolicy(),
        )
        assert assessed.classification is expected
        assert assessed.evidence_strength == pytest.approx(1.0)


def test_unmatched_evidence_is_excluded_and_cannot_force_a_decision() -> None:
    baseline = [_outcome(f"base:{index}", "base", True) for index in range(3)]
    missing = _outcome("missing:1", "missing", False)
    missing = ExecutionOutcome(
        registration=missing.registration,
        status=ExecutionOutcomeStatus.INDETERMINATE,
        correlation_status=CorrelationStatus.MISSING,
        recorded_at=NOW,
    )
    assessed = assess_service_dependency(
        "paymentservice",
        baseline,
        _cohort("paymentservice", "missing"),
        [missing],
        AssessmentPolicy(),
    )
    assert assessed.classification is ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    assert assessed.intervention.eligible_records == 0
    assert assessed.excluded_execution_ids == ("missing:1",)


def test_assessment_rejects_mixed_environments(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import scenario_service

    scenarios, executions, assessments = _repositories()
    monkeypatch.setattr(scenario_service, "_default_repository", scenarios)
    foreign = _outcome("batch:payment:foreign", "batch:payment", False)
    foreign_registration = ExecutionRegistration.from_mapping(
        {**foreign.registration.to_mapping(), "environmentId": "test-cluster-b"}
    )
    executions = InMemoryExecutionRepository()
    for batch_id, results in {
        "batch:baseline": [True, True, True],
        "batch:inventory": [True, True, False],
        "batch:email": [True, True, True],
    }.items():
        for index, succeeded in enumerate(results):
            executions.create(_outcome(f"{batch_id}:{index}", batch_id, succeeded))
    executions.create(
        ExecutionOutcome(
            registration=foreign_registration,
            status=foreign.status,
            correlation_status=foreign.correlation_status,
            recorded_at=foreign.recorded_at,
            matched_trace_id=foreign.matched_trace_id,
            evidence_id=foreign.evidence_id,
        )
    )
    with pytest.raises(ValueError, match="assessed environment"):
        create_dependency_assessment(
            _assessment_payload(),
            actor="researcher",
            repository=assessments,
            execution_repository=executions,
            now=NOW,
        )


def test_assessment_is_immutable_and_approval_projects_validated_graph(
    monkeypatch,
) -> None:
    from app.engine.workflow_dependency_modeling import scenario_service

    scenarios, executions, assessments = _repositories()
    monkeypatch.setattr(scenario_service, "_default_repository", scenarios)
    created = create_dependency_assessment(
        _assessment_payload(),
        actor="researcher",
        repository=assessments,
        execution_repository=executions,
        now=NOW,
    )
    classifications = {
        item.service_name: item.classification for item in created.services
    }
    assert classifications == {
        "emailservice": ValidatedDependencyType.OPTIONAL,
        "inventoryservice": ValidatedDependencyType.SUPPORTING,
        "paymentservice": ValidatedDependencyType.MANDATORY,
    }
    approval = approve_dependency_assessment(
        created.assessment_id,
        actor="lead-researcher",
        rationale="All controlled cohorts passed evidence gates.",
        repository=assessments,
        now=NOW + timedelta(minutes=1),
    )
    assert approval.approved_graph.mandatory_services == ["paymentservice"]
    assert approval.approved_graph.optional_services == ["emailservice"]
    assert approval.excluded_supporting_services == ("inventoryservice",)


def test_approval_rejects_unresolved_candidate(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import scenario_service

    scenarios, executions, assessments = _repositories()
    monkeypatch.setattr(scenario_service, "_default_repository", scenarios)
    payload = _assessment_payload()
    payload["interventions"] = payload["interventions"][:-1]
    created = create_dependency_assessment(
        payload,
        actor="researcher",
        repository=assessments,
        execution_repository=executions,
        now=NOW,
    )
    with pytest.raises(DependencyAssessmentNotApprovableError):
        approve_dependency_assessment(
            created.assessment_id,
            actor="lead-researcher",
            rationale="Premature approval",
            repository=assessments,
        )


def test_sqlalchemy_repository_round_trips_assessment_and_approval(
    tmp_path, monkeypatch
) -> None:
    from app.engine.workflow_dependency_modeling import scenario_service

    scenarios, executions, _ = _repositories()
    monkeypatch.setattr(scenario_service, "_default_repository", scenarios)
    engine = create_engine(f"sqlite:///{tmp_path / 'assessments.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyDependencyAssessmentRepository(engine)
    created = create_dependency_assessment(
        _assessment_payload(),
        actor="researcher",
        repository=repository,
        execution_repository=executions,
        now=NOW,
    )
    approval = approve_dependency_assessment(
        created.assessment_id,
        actor="lead-researcher",
        rationale="Evidence reviewed",
        repository=repository,
        now=NOW,
    )
    reopened = SqlAlchemyDependencyAssessmentRepository(engine)
    assert reopened.get_assessment(created.assessment_id) == created
    assert reopened.get_approval(created.assessment_id) == approval


def test_dependency_assessment_api_and_validated_graph(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import (
        dependency_assessment_service,
        execution_service,
        scenario_service,
    )

    scenarios, executions, assessments = _repositories()
    monkeypatch.setattr(scenario_service, "_default_repository", scenarios)
    monkeypatch.setattr(execution_service, "_default_repository", executions)
    monkeypatch.setattr(
        dependency_assessment_service, "_default_repository", assessments
    )
    client = TestClient(app)
    created = client.post(
        "/api/workflows/dependency-assessments",
        json=_assessment_payload(),
        headers={"X-Actor": "api-researcher"},
    )
    assert created.status_code == 201
    assessment_id = created.json()["assessmentId"]
    approved = client.post(
        f"/api/workflows/dependency-assessments/{assessment_id}/approve",
        json={"rationale": "Reviewed controlled evidence"},
        headers={"X-Actor": "api-approver"},
    )
    assert approved.status_code == 201
    graph = client.get("/api/workflows/checkout/validated-graph")
    assert graph.status_code == 200
    assert graph.json()["mandatoryServices"] == ["paymentservice"]
    assert graph.json()["optionalServices"] == ["emailservice"]


def test_dependency_assessment_http_exceptions_are_documented() -> None:
    schema = TestClient(app).get("/openapi.json").json()
    create_responses = schema["paths"]["/api/workflows/dependency-assessments"][
        "post"
    ]["responses"]
    approval_responses = schema["paths"][
        "/api/workflows/dependency-assessments/{assessment_id}/approve"
    ]["post"]["responses"]
    assert {"201", "404", "409", "422"} <= create_responses.keys()
    assert {"201", "404", "409", "422"} <= approval_responses.keys()


def test_workflow_page_contains_dependency_validation_controls() -> None:
    response = TestClient(app).get("/components/workflows")
    assert response.status_code == 200
    assert 'data-role="assessment-form"' in response.text
    assert 'data-role="dependency-assessment-list"' in response.text
    assert 'data-role="assessment-approval-rationale"' in response.text
