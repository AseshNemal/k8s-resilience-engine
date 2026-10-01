"""Phase 2 contracts for structured, immutable workflow scenarios."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.engine.workflow_dependency_modeling.models import (
    ComponentDataError,
    SuccessCondition,
)
from app.engine.workflow_dependency_modeling.scenario_repository import (
    InMemoryScenarioRepository,
    ScenarioVersionConflictError,
    SqlAlchemyScenarioRepository,
)
from app.engine.workflow_dependency_modeling.scenario_service import (
    create_scenario,
    update_scenario,
)
from app.main import app
from app.shared.persistence.models import Base


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def scenario_payload(workflow_id: str = "checkout") -> dict:
    return {
        "workflowId": workflow_id,
        "description": "Customer checkout",
        "criticality": 0.9,
        "entryService": "frontend",
        "source": "manual",
        "lifecycle": "draft",
        "changeSummary": "Initial scenario",
        "successCondition": {
            "protocol": "http",
            "terminalService": "checkoutservice",
            "operationName": "POST /checkout",
            "httpStatusCodes": [200, 201],
            "maxDurationMs": 2000,
            "requiredAttributes": [
                {"key": "business.checkout.state", "value": "completed"}
            ],
        },
    }


def test_http_condition_is_canonical_and_round_trips() -> None:
    condition = SuccessCondition.from_mapping(scenario_payload()["successCondition"])
    assert condition.http_status_codes == (200, 201)
    assert condition.max_duration_ms == 2000
    assert SuccessCondition.from_mapping(condition.to_mapping()) == condition


def test_http_condition_allows_context_specific_non_2xx_success() -> None:
    payload = scenario_payload()["successCondition"] | {"httpStatusCodes": [404]}
    condition = SuccessCondition.from_mapping(payload)
    assert condition.http_status_codes == (404,)


def test_free_text_success_condition_is_rejected() -> None:
    payload = scenario_payload() | {"successCondition": "HTTP 200 means success"}
    with pytest.raises(ComponentDataError, match="must be an object"):
        create_scenario(
            payload,
            actor="analyst@example.com",
            repository=InMemoryScenarioRepository(),
            now=NOW,
        )


@pytest.mark.parametrize("field", ["version", "createdAt", "createdBy"])
def test_create_rejects_server_managed_audit_fields(field: str) -> None:
    with pytest.raises(ValueError, match="Server-managed"):
        create_scenario(
            scenario_payload() | {field: "caller-controlled"},
            actor="analyst@example.com",
            repository=InMemoryScenarioRepository(),
            now=NOW,
        )


def test_grpc_condition_uses_canonical_status_names() -> None:
    condition = SuccessCondition.from_mapping(
        {
            "protocol": "grpc",
            "terminalService": "paymentservice",
            "operationName": "PaymentService/Charge",
            "grpcStatusCodes": ["OK"],
        }
    )
    assert condition.grpc_status_codes == ("OK",)


@pytest.mark.parametrize("status", ["ok", "SUCCESS", "17"])
def test_grpc_condition_rejects_noncanonical_status(status: str) -> None:
    with pytest.raises(ComponentDataError, match="canonical"):
        SuccessCondition.from_mapping(
            {
                "protocol": "grpc",
                "terminalService": "paymentservice",
                "operationName": "PaymentService/Charge",
                "grpcStatusCodes": [status],
            }
        )


def test_condition_rejects_mixed_protocol_outcomes() -> None:
    payload = scenario_payload()["successCondition"] | {"grpcStatusCodes": ["OK"]}
    with pytest.raises(ComponentDataError, match="cannot mix"):
        SuccessCondition.from_mapping(payload)


def test_update_creates_immutable_next_version_and_detects_stale_writer() -> None:
    repository = InMemoryScenarioRepository()
    first = create_scenario(
        scenario_payload(), actor="analyst@example.com", repository=repository, now=NOW
    )
    second = update_scenario(
        "checkout",
        {"criticality": 0.95, "changeSummary": "Reviewed criticality"},
        expected_version=1,
        actor="reviewer@example.com",
        repository=repository,
        now=NOW,
    )
    assert first.version == 1
    assert first.criticality == pytest.approx(0.9)
    assert second.version == 2
    assert second.criticality == pytest.approx(0.95)
    assert repository.get("checkout", 1) == first
    with pytest.raises(ScenarioVersionConflictError, match="current version is 2"):
        update_scenario(
            "checkout",
            {"criticality": 0.8, "changeSummary": "Stale update"},
            expected_version=1,
            actor="stale@example.com",
            repository=repository,
            now=NOW,
        )


def test_sqlalchemy_adapter_persists_versions_across_instances(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'scenarios.db'}")
    Base.metadata.create_all(engine)
    first_repository = SqlAlchemyScenarioRepository(engine)
    create_scenario(
        scenario_payload(), actor="analyst@example.com", repository=first_repository, now=NOW
    )
    update_scenario(
        "checkout",
        {"description": "Reviewed checkout", "changeSummary": "Review"},
        expected_version=1,
        actor="reviewer@example.com",
        repository=first_repository,
        now=NOW,
    )

    reopened_repository = SqlAlchemyScenarioRepository(engine)
    assert reopened_repository.get("checkout").version == 2
    assert reopened_repository.get("checkout", 1).description == "Customer checkout"


def test_api_create_read_and_versioned_update(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import scenario_service

    monkeypatch.setattr(
        scenario_service, "_default_repository", InMemoryScenarioRepository()
    )
    client = TestClient(app)
    created = client.post(
        "/api/workflows/scenarios",
        json=scenario_payload(),
        headers={"X-Actor": "analyst@example.com"},
    )
    assert created.status_code == 201
    assert created.headers["etag"] == '"checkout:1"'

    updated = client.patch(
        "/api/workflows/scenarios/checkout",
        json={"criticality": 1.0, "changeSummary": "Critical workflow review"},
        headers={
            "X-Actor": "reviewer@example.com",
            "If-Match": created.headers["etag"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.headers["etag"] == '"checkout:2"'

    old_version = client.get("/api/workflows/scenarios/checkout?version=1")
    assert old_version.status_code == 200
    assert old_version.json()["criticality"] == pytest.approx(0.9)

    stale = client.patch(
        "/api/workflows/scenarios/checkout",
        json={"criticality": 0.5, "changeSummary": "Stale"},
        headers={
            "X-Actor": "stale@example.com",
            "If-Match": created.headers["etag"],
        },
    )
    assert stale.status_code == 412
