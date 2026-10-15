"""Phase 3 execution registration, correlation, assertion, and provenance tests."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.engine.workflow_dependency_modeling.execution_models import (
    CorrelationStatus,
    ExecutionOutcomeStatus,
    ExecutionRegistration,
)
from app.engine.workflow_dependency_modeling.execution_repository import (
    InMemoryExecutionRepository,
    SqlAlchemyExecutionRepository,
)
from app.engine.workflow_dependency_modeling.execution_service import (
    _registration,
    run_benchmark_batch,
)
from app.engine.workflow_dependency_modeling.models import WorkflowScenario
from app.engine.workflow_dependency_modeling.outcome_evaluator import evaluate_execution
from app.main import app
from app.shared.persistence.models import Base


NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "reserved_field",
    [
        {"source": "controlled_workflow_experiment"},
        {"experimentRecordId": "manifest:forged"},
    ],
)
def test_external_execution_cannot_claim_controlled_campaign_provenance(reserved_field) -> None:
    payload = registration().to_mapping()
    payload.pop("registeredBy")
    payload.update(reserved_field)
    with pytest.raises(ValueError, match="reserved for server-run trials"):
        _registration(payload, "external-operator")


def scenario(
    *,
    protocol: str = "http",
    outcomes: list | None = None,
    max_duration_ms: int = 100,
    required_attributes: list | None = None,
) -> WorkflowScenario:
    success = {
        "protocol": protocol,
        "terminalService": "paymentservice",
        "operationName": "PaymentService/Charge",
        "maxDurationMs": max_duration_ms,
        "requiredAttributes": required_attributes or [],
    }
    key = {
        "http": "httpStatusCodes",
        "grpc": "grpcStatusCodes",
        "span": "spanStatusCodes",
    }[protocol]
    success[key] = outcomes or ({"http": [200], "grpc": ["OK"], "span": ["OK"]}[protocol])
    return WorkflowScenario.from_mapping(
        {
            "workflowId": "checkout",
            "description": "Checkout",
            "criticality": 0.9,
            "entryService": "frontend",
            "successCondition": success,
        }
    )


def registration(
    *,
    invocation_status: str = "completed",
    trace_id: str | None = "trace-1",
) -> ExecutionRegistration:
    payload = {
        "executionId": f"execution:{invocation_status}",
        "batchId": "batch:one",
        "workflowId": "checkout",
        "scenarioVersion": 1,
        "invocationStatus": invocation_status,
        "source": "test",
        "evidenceLocator": "memory://phase3",
        "startedAt": "2026-10-03T00:00:00Z",
        "finishedAt": "2026-10-03T00:00:01Z",
        "registeredBy": "tester",
    }
    if trace_id is not None:
        payload["expectedTraceId"] = trace_id
    return ExecutionRegistration.from_mapping(payload)


def trace(
    *,
    trace_id: str = "trace-1",
    tags: list[dict] | None = None,
    duration_us: int = 50_000,
    error: bool = False,
) -> dict:
    resolved_tags = tags or [
        {"key": "http.response.status_code", "type": "int64", "value": 200},
    ]
    resolved_tags.append({"key": "error", "type": "bool", "value": error})
    return {
        "traceID": trace_id,
        "spans": [
            {
                "traceID": trace_id,
                "spanID": "terminal-1",
                "operationName": "PaymentService/Charge",
                "references": [],
                "startTime": 1_700_000_000_000_000,
                "duration": duration_us,
                "tags": resolved_tags,
                "processID": "payment",
            }
        ],
        "processes": {"payment": {"serviceName": "paymentservice", "tags": []}},
    }


def test_completed_execution_records_success_and_evidence() -> None:
    result = evaluate_execution(registration(), scenario(), [trace()], now=NOW)
    assert result.status is ExecutionOutcomeStatus.SUCCEEDED
    assert result.correlation_status is CorrelationStatus.MATCHED
    assert result.matched_trace_id == "trace-1"
    assert result.evidence_id == "trace:trace-1"
    assert all(item.passed for item in result.assertions)


def test_http_success_matches_semantic_route_for_generic_server_span() -> None:
    configured = scenario()
    configured_payload = configured.to_mapping()
    configured_payload["successCondition"].update(
        {
            "terminalService": "frontend",
            "operationName": "POST /cart/checkout",
        }
    )
    evidence = trace(
        tags=[
            {"key": "http.request.method", "type": "string", "value": "POST"},
            {"key": "http.route", "type": "string", "value": "/cart/checkout"},
            {"key": "http.response.status_code", "type": "int64", "value": 200},
        ]
    )
    evidence["processes"]["payment"]["serviceName"] = "frontend"
    evidence["spans"][0]["operationName"] = "frontend"

    result = evaluate_execution(
        registration(),
        WorkflowScenario.from_mapping(configured_payload),
        [evidence],
        now=NOW,
    )

    assert result.status is ExecutionOutcomeStatus.SUCCEEDED
    assert result.correlation_status is CorrelationStatus.MATCHED


def test_missing_trace_remains_visible_as_indeterminate() -> None:
    result = evaluate_execution(registration(), scenario(), [], now=NOW)
    assert result.status is ExecutionOutcomeStatus.INDETERMINATE
    assert result.correlation_status is CorrelationStatus.MISSING
    assert result.evidence_id is None


def test_duplicate_trace_id_is_ambiguous_not_silently_selected() -> None:
    result = evaluate_execution(registration(), scenario(), [trace(), trace()], now=NOW)
    assert result.status is ExecutionOutcomeStatus.INDETERMINATE
    assert result.correlation_status is CorrelationStatus.AMBIGUOUS


@pytest.mark.parametrize(
    ("invocation_status", "expected"),
    [
        ("failed", ExecutionOutcomeStatus.FAILED),
        ("timed_out", ExecutionOutcomeStatus.TIMED_OUT),
    ],
)
def test_failed_and_timed_out_invocations_are_recorded_without_trace(
    invocation_status: str,
    expected: ExecutionOutcomeStatus,
) -> None:
    result = evaluate_execution(
        registration(invocation_status=invocation_status, trace_id=None),
        scenario(),
        [],
        now=NOW,
    )
    assert result.status is expected
    assert result.correlation_status is CorrelationStatus.NOT_ATTEMPTED


def test_protocol_duration_and_attribute_failures_are_explained() -> None:
    configured = scenario(
        max_duration_ms=10,
        required_attributes=[{"key": "business.result", "value": "approved"}],
    )
    evidence = trace(
        duration_us=50_000,
        tags=[
            {"key": "http.response.status_code", "type": "int64", "value": 500},
            {"key": "business.result", "type": "string", "value": "declined"},
        ],
    )
    result = evaluate_execution(registration(), configured, [evidence], now=NOW)
    assert result.status is ExecutionOutcomeStatus.FAILED
    assert [item.name for item in result.assertions] == [
        "protocol_status",
        "terminal_duration_ms",
        "attribute:business.result",
    ]
    assert not any(item.passed for item in result.assertions)


def test_numeric_grpc_status_is_normalized_to_canonical_name() -> None:
    evidence = trace(
        tags=[{"key": "rpc.grpc.status_code", "type": "int64", "value": 0}]
    )
    result = evaluate_execution(
        registration(), scenario(protocol="grpc"), [evidence], now=NOW
    )
    assert result.status is ExecutionOutcomeStatus.SUCCEEDED
    assert result.assertions[0].observed == "OK"


@pytest.mark.parametrize(
    ("workflow_id", "trace_count"),
    [
        ("checkout", 3),
        ("add_to_cart", 2),
        ("refund", 3),
        ("view_product", 3),
    ],
)
def test_benchmark_batch_records_each_trace_with_explicit_provenance(
    workflow_id: str,
    trace_count: int,
) -> None:
    repository = InMemoryExecutionRepository()
    batch_id = f"benchmark:{workflow_id}:test"
    outcomes = run_benchmark_batch(
        workflow_id,
        actor="benchmark-runner",
        batch_id=batch_id,
        repository=repository,
        now=NOW,
    )
    assert len(outcomes) == trace_count
    assert {item.status for item in outcomes} == {ExecutionOutcomeStatus.SUCCEEDED}
    assert all(item.evidence_id for item in outcomes)
    assert len(repository.list(batch_id=batch_id)) == trace_count


def test_sqlalchemy_repository_round_trips_outcome(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'executions.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyExecutionRepository(engine)
    outcome = evaluate_execution(registration(), scenario(), [trace()], now=NOW)
    repository.create(outcome)
    reopened = SqlAlchemyExecutionRepository(engine)
    assert reopened.get("execution:completed") == outcome


def test_benchmark_api_creates_and_lists_batch(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import execution_service

    monkeypatch.setattr(
        execution_service,
        "_default_repository",
        InMemoryExecutionRepository(),
    )
    client = TestClient(app)
    created = client.post(
        "/api/workflows/execution-batches/benchmarks/checkout",
        params={"batch_id": "benchmark:checkout:api"},
        headers={"X-Actor": "api-test"},
    )
    assert created.status_code == 201
    assert len(created.json()) == 3
    listed = client.get(
        "/api/workflows/executions",
        params={"batch_id": "benchmark:checkout:api"},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 3


def test_external_jaeger_registration_correlates_exact_trace(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import execution_service, scenario_service
    from app.engine.workflow_dependency_modeling.scenario_repository import (
        InMemoryScenarioRepository,
    )

    scenario_repository = InMemoryScenarioRepository()
    scenario_repository.create(scenario())
    monkeypatch.setattr(scenario_service, "_default_repository", scenario_repository)
    monkeypatch.setattr(
        execution_service,
        "_default_repository",
        InMemoryExecutionRepository(),
    )
    monkeypatch.setattr(
        execution_service.JaegerClient,
        "fetch_traces",
        lambda self, **kwargs: [trace()],
    )
    client = TestClient(app)
    payload = registration().to_mapping()
    payload.pop("registeredBy")
    created = client.post(
        "/api/workflows/executions/jaeger",
        json=payload,
        headers={"X-Actor": "external-runner"},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "succeeded"
    assert created.json()["evidenceId"] == "trace:trace-1"


def test_execution_http_exceptions_are_documented_in_openapi() -> None:
    schema = TestClient(app).get("/openapi.json").json()
    benchmark_responses = schema["paths"][
        "/api/workflows/execution-batches/benchmarks/{workflow_id}"
    ]["post"]["responses"]
    jaeger_responses = schema["paths"]["/api/workflows/executions/jaeger"]["post"][
        "responses"
    ]
    assert {"201", "404", "409", "422"} <= benchmark_responses.keys()
    assert {"201", "404", "409", "422", "503"} <= jaeger_responses.keys()
