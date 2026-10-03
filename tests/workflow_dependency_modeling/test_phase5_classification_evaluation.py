"""Phase 5 independent reference and multiclass evaluation tests."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.engine.workflow_dependency_modeling.classification_evaluation_models import (
    DependencyClassificationReference,
)
from app.engine.workflow_dependency_modeling.classification_evaluation_repository import (
    ClassificationArtifactConflictError,
    InMemoryClassificationEvaluationRepository,
    SqlAlchemyClassificationEvaluationRepository,
)
from app.engine.workflow_dependency_modeling.classification_evaluation_service import (
    create_dependency_classification_evaluation,
    create_dependency_classification_reference,
)
from app.engine.workflow_dependency_modeling.classification_evaluator import (
    evaluate_dependency_classification,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    DependencyAssessment,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_repository import (
    InMemoryDependencyAssessmentRepository,
)
from app.engine.workflow_dependency_modeling.models import WorkflowScenario
from app.engine.workflow_dependency_modeling.scenario_repository import (
    InMemoryScenarioRepository,
)
from app.main import app
from app.shared.persistence.models import Base


NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


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
                "operationName": "payment.charge",
                "httpStatusCodes": [200],
            },
        }
    )


def _assessment(*, abstain: bool = False) -> DependencyAssessment:
    services = [
        {
            "serviceName": "paymentservice",
            "classification": "mandatory",
            "baseline": {"totalRecords": 3, "eligibleRecords": 3, "excludedRecords": 0, "succeededRecords": 3, "failedRecords": 0, "successRate": 1.0},
            "intervention": {"totalRecords": 3, "eligibleRecords": 3, "excludedRecords": 0, "succeededRecords": 0, "failedRecords": 3, "successRate": 0.0},
            "interventionType": "service_unavailable",
            "failureEffect": 1.0,
            "evidenceStrength": 1.0,
            "reasons": ["repeatable failure"],
        },
        {
            "serviceName": "emailservice",
            "classification": "insufficient_evidence" if abstain else "optional",
            "baseline": {"totalRecords": 3, "eligibleRecords": 3, "excludedRecords": 0, "succeededRecords": 3, "failedRecords": 0, "successRate": 1.0},
            "intervention": {"totalRecords": 3, "eligibleRecords": 0 if abstain else 3, "excludedRecords": 3 if abstain else 0, "succeededRecords": 0 if abstain else 3, "failedRecords": 0, "successRate": None if abstain else 1.0},
            "interventionType": "service_unavailable",
            "failureEffect": None if abstain else 0.0,
            "evidenceStrength": 0.0 if abstain else 1.0,
            "reasons": ["evidence result"],
        },
        {
            "serviceName": "inventoryservice",
            "classification": "supporting",
            "baseline": {"totalRecords": 3, "eligibleRecords": 3, "excludedRecords": 0, "succeededRecords": 3, "failedRecords": 0, "successRate": 1.0},
            "intervention": {"totalRecords": 3, "eligibleRecords": 3, "excludedRecords": 0, "succeededRecords": 2, "failedRecords": 1, "successRate": 0.6667},
            "interventionType": "degraded_response",
            "failureEffect": 0.3333,
            "evidenceStrength": 1.0,
            "reasons": ["partial degradation"],
        },
    ]
    return DependencyAssessment.from_mapping(
        {
            "assessmentId": "assessment:checkout:phase5",
            "workflowId": "checkout",
            "scenarioVersion": 1,
            "environmentId": "test-cluster-a",
            "source": "controlled_experiment",
            "candidateGraph": {
                "workflowId": "checkout",
                "criticality": 0.9,
                "mandatoryServices": ["paymentservice", "inventoryservice"],
                "optionalServices": ["emailservice"],
                "edges": [],
            },
            "baselineBatchIds": ["baseline"],
            "interventions": [],
            "policy": {},
            "services": services,
            "createdAt": "2026-10-03T00:00:00Z",
            "createdBy": "researcher",
        }
    )


def _reference() -> DependencyClassificationReference:
    return DependencyClassificationReference.from_mapping(
        {
            "referenceId": "checkout:classification-reference:v1",
            "workflowId": "checkout",
            "scenarioVersion": 1,
            "referenceVersion": 1,
            "labels": [
                {"serviceName": "paymentservice", "classification": "mandatory", "justification": "Payment is required"},
                {"serviceName": "emailservice", "classification": "optional", "justification": "Email is non-blocking"},
                {"serviceName": "inventoryservice", "classification": "supporting", "justification": "Inventory degrades the workflow"},
            ],
            "source": "independent_domain_review",
            "synthetic": True,
            "createdAt": "2026-10-03T00:00:00Z",
            "createdBy": "domain-reviewer",
        }
    )


def test_perfect_multiclass_evaluation() -> None:
    result = evaluate_dependency_classification(
        _assessment(), _reference(), evaluation_id="evaluation:perfect", actor="tester", now=NOW
    )
    assert result.accuracy == pytest.approx(1.0)
    assert result.macro_f1 == pytest.approx(1.0)
    assert result.micro_f1 == pytest.approx(1.0)
    assert result.coverage == pytest.approx(1.0)
    assert result.abstention_rate == pytest.approx(0.0)
    assert result.confusion_matrix["mandatory"]["mandatory"] == 1


def test_abstention_reduces_coverage_without_becoming_misclassification() -> None:
    result = evaluate_dependency_classification(
        _assessment(abstain=True),
        _reference(),
        evaluation_id="evaluation:abstain",
        actor="tester",
        now=NOW,
    )
    assert result.accuracy == pytest.approx(1.0)
    assert result.coverage == pytest.approx(2 / 3)
    assert result.abstention_rate == pytest.approx(1 / 3)
    assert result.abstained_services == ("emailservice",)
    assert result.misclassified_services == ()


def test_reference_versions_are_immutable_and_optimistic(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import scenario_service

    scenarios = InMemoryScenarioRepository()
    scenarios.create(_scenario())
    monkeypatch.setattr(scenario_service, "_default_repository", scenarios)
    repository = InMemoryClassificationEvaluationRepository()
    payload = _reference().to_mapping()
    for field in ("referenceId", "referenceVersion", "createdAt", "createdBy"):
        payload.pop(field)
    created = create_dependency_classification_reference(
        payload, actor="reviewer", expected_version=0, repository=repository, now=NOW
    )
    assert created.reference_version == 1
    with pytest.raises(ClassificationArtifactConflictError):
        create_dependency_classification_reference(
            payload, actor="reviewer", expected_version=0, repository=repository, now=NOW
        )


def test_evaluation_service_loads_frozen_assessment_before_reference(monkeypatch) -> None:
    assessments = InMemoryDependencyAssessmentRepository()
    assessments.create_assessment(_assessment())
    repository = InMemoryClassificationEvaluationRepository()
    repository.add_reference(_reference(), 0)
    calls: list[str] = []
    original_assessment = assessments.get_assessment
    original_reference = repository.get_reference
    monkeypatch.setattr(assessments, "get_assessment", lambda value: (calls.append("assessment"), original_assessment(value))[1])
    monkeypatch.setattr(repository, "get_reference", lambda workflow, version=None: (calls.append("reference"), original_reference(workflow, version))[1])
    result = create_dependency_classification_evaluation(
        {"evaluationId": "evaluation:ordered", "assessmentId": "assessment:checkout:phase5", "referenceVersion": 1},
        actor="evaluator",
        repository=repository,
        assessment_repository=assessments,
        now=NOW,
    )
    assert result.accuracy == pytest.approx(1.0)
    assert calls == ["assessment", "reference"]


def test_sqlalchemy_round_trip(monkeypatch, tmp_path) -> None:
    from app.engine.workflow_dependency_modeling import scenario_service

    scenarios = InMemoryScenarioRepository()
    scenarios.create(_scenario())
    monkeypatch.setattr(scenario_service, "_default_repository", scenarios)
    engine = create_engine(f"sqlite:///{tmp_path / 'phase5.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyClassificationEvaluationRepository(engine)
    repository.add_reference(_reference(), 0)
    assessments = InMemoryDependencyAssessmentRepository()
    assessments.create_assessment(_assessment())
    evaluation = create_dependency_classification_evaluation(
        {"evaluationId": "evaluation:sql", "assessmentId": "assessment:checkout:phase5"},
        actor="evaluator",
        repository=repository,
        assessment_repository=assessments,
        now=NOW,
    )
    reopened = SqlAlchemyClassificationEvaluationRepository(engine)
    assert reopened.get_reference("checkout", 1) == _reference()
    assert reopened.get_evaluation("evaluation:sql") == evaluation


def test_reference_and_evaluation_api(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import (
        classification_evaluation_service,
        dependency_assessment_service,
        scenario_service,
    )

    scenarios = InMemoryScenarioRepository()
    scenarios.create(_scenario())
    assessments = InMemoryDependencyAssessmentRepository()
    assessments.create_assessment(_assessment())
    repository = InMemoryClassificationEvaluationRepository()
    monkeypatch.setattr(scenario_service, "_default_repository", scenarios)
    monkeypatch.setattr(dependency_assessment_service, "_default_repository", assessments)
    monkeypatch.setattr(classification_evaluation_service, "_default_repository", repository)
    client = TestClient(app)
    payload = _reference().to_mapping()
    for field in ("referenceId", "referenceVersion", "createdAt", "createdBy"):
        payload.pop(field)
    reference = client.post(
        "/api/workflows/dependency-references",
        json=payload,
        headers={"X-Actor": "api-reviewer"},
    )
    assert reference.status_code == 201
    evaluation = client.post(
        "/api/workflows/dependency-evaluations",
        json={"evaluationId": "evaluation:api", "assessmentId": "assessment:checkout:phase5"},
        headers={"X-Actor": "api-evaluator"},
    )
    assert evaluation.status_code == 201
    assert evaluation.json()["macroF1"] == pytest.approx(1.0)


def test_phase5_http_exceptions_are_documented() -> None:
    schema = TestClient(app).get("/openapi.json").json()
    reference = schema["paths"]["/api/workflows/dependency-references"]["post"]["responses"]
    evaluation = schema["paths"]["/api/workflows/dependency-evaluations"]["post"]["responses"]
    assert {"201", "404", "409", "422"} <= reference.keys()
    assert {"201", "404", "409", "422"} <= evaluation.keys()
