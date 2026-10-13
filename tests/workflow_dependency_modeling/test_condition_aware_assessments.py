"""Condition-aware dependency decisions and trace provenance."""

from datetime import datetime, timezone

import pytest

from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    ValidatedDependencyType,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_repository import (
    DependencyAssessmentNotApprovableError,
    InMemoryDependencyAssessmentRepository,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_service import (
    approve_dependency_assessment,
    build_condition_scoped_workflow_graph,
    create_dependency_assessment,
)
from app.engine.workflow_dependency_modeling.execution_models import (
    CorrelationStatus,
    ExecutionOutcome,
    ExecutionOutcomeStatus,
    ExecutionRegistration,
    TraceEvidenceSnapshot,
)
from app.engine.workflow_dependency_modeling.execution_repository import (
    InMemoryExecutionRepository,
)
from app.engine.workflow_dependency_modeling.fixture_repository import (
    get_scenario,
    load_fixture_traces,
)
from app.engine.workflow_dependency_modeling.models import WorkflowScenario
from app.engine.workflow_dependency_modeling.outcome_evaluator import evaluate_execution
from app.engine.workflow_dependency_modeling.scenario_repository import (
    InMemoryScenarioRepository,
)


NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def _outcome(
    execution_id: str, batch_id: str, condition_id: str, succeeded: bool
) -> ExecutionOutcome:
    registration = ExecutionRegistration.from_mapping(
        {
            "executionId": execution_id,
            "batchId": batch_id,
            "workflowId": "checkout",
            "scenarioVersion": 1,
            "conditionId": condition_id,
            "invocationStatus": "completed",
            "source": "controlled_experiment",
            "evidenceLocator": f"jaeger://trace/{execution_id}",
            "environmentId": "test-cluster-a",
            "expectedTraceId": f"trace:{execution_id}",
            "startedAt": "2026-10-13T00:00:00Z",
            "finishedAt": "2026-10-13T00:00:01Z",
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
        evidence_id=f"evidence:{execution_id}",
    )


def _scenario() -> WorkflowScenario:
    return WorkflowScenario.from_mapping(
        {
            "workflowId": "checkout",
            "description": "Checkout",
            "criticality": 0.9,
            "entryService": "frontend",
            "conditions": [
                {"conditionId": "guest", "description": "Guest checkout"},
                {"conditionId": "returning", "description": "Returning customer"},
            ],
            "successCondition": {
                "protocol": "http",
                "terminalService": "paymentservice",
                "operationName": "POST /charge",
                "httpStatusCodes": [200],
            },
        }
    )


def test_condition_aware_classification_and_scoped_projection(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import scenario_service

    scenarios = InMemoryScenarioRepository()
    scenarios.create(_scenario())
    monkeypatch.setattr(scenario_service, "_default_repository", scenarios)
    executions = InMemoryExecutionRepository()
    for condition_id in ("guest", "returning"):
        for index in range(3):
            executions.create(
                _outcome(
                    f"baseline:{condition_id}:{index}",
                    "batch:baseline",
                    condition_id,
                    True,
                )
            )
    for index in range(3):
        executions.create(
            _outcome(f"guest:{index}", "batch:guest", "guest", False)
        )
        executions.create(
            _outcome(
                f"returning:{index}", "batch:returning", "returning", True
            )
        )

    repository = InMemoryDependencyAssessmentRepository()
    assessment = create_dependency_assessment(
        {
            "assessmentId": "assessment:checkout:conditions",
            "workflowId": "checkout",
            "scenarioVersion": 1,
            "environmentId": "test-cluster-a",
            "source": "controlled_experiment",
            "candidateGraph": {
                "workflowId": "checkout",
                "criticality": 0.9,
                "mandatoryServices": ["paymentservice"],
                "optionalServices": [],
                "edges": [],
            },
            "baselineBatchIds": ["batch:baseline"],
            "interventions": [
                {
                    "targetService": "paymentservice",
                    "conditionId": "guest",
                    "interventionType": "service_unavailable",
                    "batchIds": ["batch:guest"],
                    "interventionConfirmed": True,
                    "confirmationEvidence": "chaos-run:guest",
                },
                {
                    "targetService": "paymentservice",
                    "conditionId": "returning",
                    "interventionType": "service_unavailable",
                    "batchIds": ["batch:returning"],
                    "interventionConfirmed": True,
                    "confirmationEvidence": "chaos-run:returning",
                },
            ],
        },
        actor="researcher",
        repository=repository,
        execution_repository=executions,
        now=NOW,
    )

    payment = assessment.services[0]
    assert payment.classification is ValidatedDependencyType.CONDITIONAL
    assert [item.condition_id for item in payment.condition_results] == [
        "guest",
        "returning",
    ]
    assert [item.classification for item in payment.condition_results] == [
        ValidatedDependencyType.MANDATORY,
        ValidatedDependencyType.OPTIONAL,
    ]
    assert len(payment.baseline.eligible_execution_ids) == 6
    assert len(payment.baseline.eligible_trace_ids) == 6
    assert assessment.to_mapping()["conditionalServices"] == ["paymentservice"]

    with pytest.raises(
        DependencyAssessmentNotApprovableError,
        match="condition-scoped graph export",
    ):
        approve_dependency_assessment(
            assessment.assessment_id,
            actor="lead-researcher",
            rationale="Cannot flatten a conditional result",
            repository=repository,
        )

    guest = build_condition_scoped_workflow_graph(
        assessment.assessment_id, "guest", repository
    )
    returning = build_condition_scoped_workflow_graph(
        assessment.assessment_id, "returning", repository
    )
    assert guest.mandatory_services == ["paymentservice"]
    assert returning.optional_services == ["paymentservice"]


def test_execution_condition_defaults_for_existing_payloads() -> None:
    outcome = _outcome("run:1", "batch:1", "guest", True)
    assert ExecutionOutcome.from_mapping(outcome.to_mapping()) == outcome
    payload = outcome.registration.to_mapping()
    payload.pop("conditionId")
    assert ExecutionRegistration.from_mapping(payload).condition_id == "default"


def test_raw_trace_snapshot_is_canonical_immutable_and_tamper_evident() -> None:
    raw_trace = {"traceID": "abc", "spans": [{"spanID": "1", "tags": []}]}
    snapshot = TraceEvidenceSnapshot.from_trace(
        raw_trace,
        trace_id="abc",
        source_locator="jaeger://trace/abc",
        captured_at=NOW,
    )
    raw_trace["spans"].append({"spanID": "2"})
    persisted = snapshot.to_mapping()
    assert persisted["rawTrace"]["spans"] == [{"spanID": "1", "tags": []}]
    assert TraceEvidenceSnapshot.from_mapping(persisted) == snapshot

    persisted["rawTrace"]["spans"][0]["spanID"] = "tampered"
    with pytest.raises(ValueError, match="checksum"):
        TraceEvidenceSnapshot.from_mapping(persisted)


def test_matched_execution_persists_the_exact_trace_snapshot() -> None:
    scenario = get_scenario("checkout")
    traces = load_fixture_traces("checkout")
    trace_id = traces[0]["traceID"]
    registration = ExecutionRegistration.from_mapping(
        {
            "executionId": "snapshot:checkout:1",
            "batchId": "snapshot:checkout",
            "workflowId": "checkout",
            "scenarioVersion": scenario.version,
            "invocationStatus": "completed",
            "source": "benchmark_fixture",
            "evidenceLocator": "sample_data/traces/checkout.json",
            "environmentId": "synthetic-fixture",
            "expectedTraceId": trace_id,
            "startedAt": "2026-10-13T00:00:00Z",
            "finishedAt": "2026-10-13T00:00:01Z",
            "registeredBy": "test-runner",
        }
    )
    outcome = evaluate_execution(registration, scenario, traces, now=NOW)
    assert outcome.evidence_snapshot is not None
    assert outcome.evidence_snapshot.trace_id == trace_id
    assert outcome.evidence_snapshot.to_mapping()["rawTrace"] == traces[0]
    assert ExecutionOutcome.from_mapping(outcome.to_mapping()) == outcome
