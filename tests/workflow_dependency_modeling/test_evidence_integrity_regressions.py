"""Regression cases for application-independent evidence safety."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.engine.workflow_dependency_modeling.dependency_assessor import _statistics
from app.engine.workflow_dependency_modeling.execution_models import (
    CorrelationStatus, ExecutionOutcome, ExecutionOutcomeStatus,
    ExecutionRegistration, TraceEvidenceSnapshot,
)
from app.engine.workflow_dependency_modeling.trace_validator import (
    TraceQuality, eligible_traces, validate_trace_quality,
)
from app.engine.workflow_dependency_modeling.workflow_onboarding import build_workflow_probe


def span(identifier, parent=None):
    return {"spanID": identifier, "processID": "p", "references": (
        [{"refType": "CHILD_OF", "spanID": parent}] if parent else []
    )}


def trace(spans):
    return {"traceID": "trace-a", "spans": spans, "processes": {"p": {"serviceName": "gateway"}}}


def test_cycle_disconnected_from_valid_root_is_invalid():
    result = validate_trace_quality([trace([span("root"), span("a", "b"), span("b", "a")])])
    assert result.results[0].quality is TraceQuality.INVALID
    assert "Trace contains a parent cycle" in result.results[0].reasons


def test_links_do_not_repair_disconnected_trace():
    linked = span("worker")
    linked["references"] = [{"refType": "SPAN_LINK", "spanID": "root", "traceID": "other-trace"}]
    result = validate_trace_quality([trace([span("root"), linked])])
    assert result.results[0].quality is TraceQuality.PARTIAL


def test_duplicate_trace_ids_never_enter_primary_evidence():
    traces = [trace([span("root")]), trace([])]
    summary = validate_trace_quality(traces)
    assert summary.invalid_count == 2
    assert eligible_traces(traces, summary) == []


@pytest.mark.parametrize("spans", [[None], [span("root"), {**span("child", "root"), "traceID": "other"}]])
def test_malformed_or_cross_trace_spans_are_invalid(spans):
    assert validate_trace_quality([trace(spans)]).results[0].quality is TraceQuality.INVALID


def outcome():
    registration = ExecutionRegistration.from_mapping({
        "executionId": "execution:1", "batchId": "baseline:1", "workflowId": "orders",
        "scenarioVersion": 1, "invocationStatus": "completed", "source": "test",
        "evidenceLocator": "fixture://trace-a", "environmentId": "local-test",
        "expectedTraceId": "trace-a", "startedAt": "2026-10-13T00:00:00Z",
        "finishedAt": "2026-10-13T00:00:01Z", "registeredBy": "tester",
    })
    return ExecutionOutcome(registration, ExecutionOutcomeStatus.SUCCEEDED,
                            CorrelationStatus.MATCHED, datetime.now(timezone.utc), matched_trace_id="trace-a")


def test_duplicated_executions_do_not_inflate_cohort():
    record = outcome()
    stats, excluded = _statistics([record, record, record])
    assert stats.eligible_records == 0
    assert len(excluded) == 3


def test_mismatched_trace_cannot_count_as_matched_evidence():
    stats, _ = _statistics([replace(outcome(), matched_trace_id="unrelated")])
    assert stats.eligible_records == 0


def test_snapshot_cannot_relabel_another_trace():
    with pytest.raises(ValueError, match="does not match"):
        TraceEvidenceSnapshot.from_trace(trace([span("root")]), trace_id="other",
                                         source_locator="fixture://x", captured_at=datetime.now(timezone.utc))


def test_generic_parameterized_write_workflow_accepts_ui_request_shape():
    definition = build_workflow_probe("orders-api", "POST /orders/{id}", {
        "mode": "observed_entry", "expectedStatusCodes": [201],
        "request": {"pathVariables": {"id": "test-order"}, "jsonBody": {"quantity": 2},
                    "responseContains": ["confirmed"]},
    })
    data = definition.to_mapping()
    assert data["steps"][0]["path"] == "/orders/{{id}}"
    assert data["steps"][0]["responseContains"] == ["confirmed"]


def test_terminal_absence_is_not_a_workflow_failure():
    from app.engine.workflow_dependency_modeling.models import WorkflowScenario
    from app.engine.workflow_dependency_modeling.outcome_evaluator import evaluate_execution

    scenario = WorkflowScenario.from_mapping({
        "workflowId": "orders", "description": "Create order", "criticality": 0.8,
        "entryService": "gateway", "successCondition": {"protocol": "http",
            "terminalService": "orders-api", "operationName": "POST /orders", "httpStatusCodes": [201]},
    })
    result = evaluate_execution(outcome().registration, scenario, [trace([span("root")])])
    assert result.status is ExecutionOutcomeStatus.INDETERMINATE
    assert result.evidence_snapshot is not None


def test_same_trace_in_both_cohorts_blocks_classification():
    from app.engine.workflow_dependency_modeling.dependency_assessor import assess_service_dependency
    from app.engine.workflow_dependency_modeling.dependency_assessment_models import AssessmentPolicy, InterventionCohort

    intervention = InterventionCohort.from_mapping({"targetService": "orders-api", "interventionType": "service_unavailable",
        "batchIds": ["intervention:1"], "interventionConfirmed": True, "confirmationEvidence": "test evidence"})
    result = assess_service_dependency("orders-api", [outcome()], intervention, [outcome()], AssessmentPolicy(min_samples_per_cohort=1))
    assert result.classification.value == "insufficient_evidence"
    assert any("reuse" in reason for reason in result.reasons)
