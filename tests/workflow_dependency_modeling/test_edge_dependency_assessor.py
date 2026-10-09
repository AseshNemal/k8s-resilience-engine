"""Evidence-level tests for explicit mandatory-edge classification."""

from datetime import datetime, timezone

import pytest

from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    AssessmentPolicy,
    EdgeInterventionCohort,
    ValidatedDependencyType,
)
from app.engine.workflow_dependency_modeling.dependency_assessor import (
    assess_edge_dependency,
)
from app.engine.workflow_dependency_modeling.execution_models import (
    CorrelationStatus,
    ExecutionOutcome,
    ExecutionOutcomeStatus,
    ExecutionRegistration,
)


NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def _outcome(execution_id: str, succeeded: bool) -> ExecutionOutcome:
    return ExecutionOutcome(
        registration=ExecutionRegistration.from_mapping(
            {
                "executionId": execution_id,
                "batchId": execution_id.split(":")[0],
                "workflowId": "fund_transfer",
                "scenarioVersion": 1,
                "invocationStatus": "completed",
                "source": "controlled_experiment",
                "evidenceLocator": f"jaeger:{execution_id}",
                "environmentId": "research-environment",
                "startedAt": "2026-10-09T00:00:00Z",
                "finishedAt": "2026-10-09T00:00:01Z",
                "expectedTraceId": f"trace-{execution_id}",
                "registeredBy": "researcher",
            }
        ),
        status=(
            ExecutionOutcomeStatus.SUCCEEDED
            if succeeded
            else ExecutionOutcomeStatus.FAILED
        ),
        correlation_status=CorrelationStatus.MATCHED,
        matched_trace_id=f"trace-{execution_id}",
        evidence_id=f"trace:trace-{execution_id}",
        recorded_at=NOW,
    )


def _intervention(source: str, target: str) -> EdgeInterventionCohort:
    return EdgeInterventionCohort.from_mapping(
        {
            "sourceService": source,
            "targetService": target,
            "interventionType": "call_omitted",
            "batchIds": ["edge-intervention"],
            "interventionConfirmed": True,
            "confirmationEvidence": "experiment-run:edge-intervention",
        }
    )


def test_classifies_edge_from_its_own_intervention_outcomes() -> None:
    baseline = [_outcome(f"baseline:{index}", True) for index in range(3)]
    intervention = [_outcome(f"edge:{index}", False) for index in range(3)]

    result = assess_edge_dependency(
        "transfer-service",
        "ledger-service",
        baseline,
        _intervention("transfer-service", "ledger-service"),
        intervention,
        AssessmentPolicy(),
    )

    assert result.edge == ("transfer-service", "ledger-service")
    assert result.classification is ValidatedDependencyType.MANDATORY
    assert result.failure_effect == pytest.approx(1.0)
    assert result.evidence_strength == pytest.approx(1.0)


def test_does_not_infer_edge_label_without_edge_intervention() -> None:
    baseline = [_outcome(f"baseline:{index}", True) for index in range(3)]

    result = assess_edge_dependency(
        "transfer-service",
        "ledger-service",
        baseline,
        None,
        [],
        AssessmentPolicy(),
    )

    assert result.classification is ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    assert "candidate edge" in result.reasons[0]


def test_rejects_intervention_for_a_different_edge() -> None:
    with pytest.raises(ValueError, match="does not match"):
        assess_edge_dependency(
            "transfer-service",
            "ledger-service",
            [_outcome(f"baseline:{index}", True) for index in range(3)],
            _intervention("transfer-service", "audit-service"),
            [_outcome(f"edge:{index}", False) for index in range(3)],
            AssessmentPolicy(),
        )


def test_edge_artifacts_round_trip_without_benchmark_specific_names() -> None:
    intervention = _intervention("orders-api", "risk-engine")
    restored = EdgeInterventionCohort.from_mapping(intervention.to_mapping())

    assert restored == intervention
