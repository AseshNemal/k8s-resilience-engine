"""Guided analysis-session tests for experimental and production evidence."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    InMemoryAnalysisSessionRepository,
    SqlAlchemyAnalysisSessionRepository,
)
from app.engine.workflow_dependency_modeling.analysis_session_service import (
    discover_analysis_workflows,
    start_workflow_analysis,
)
from app.main import app
from app.shared.persistence.models import Base


NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def _evidence() -> dict:
    return {
        "workflowId": "POST /cart/checkout",
        "observedServices": ["checkoutservice", "frontend", "paymentservice"],
        "serviceEvidence": [
            {"service": "frontend", "traceCount": 12, "frequency": 1.0},
            {"service": "checkoutservice", "traceCount": 12, "frequency": 1.0},
            {"service": "paymentservice", "traceCount": 12, "frequency": 1.0},
        ],
        "traceCount": 12,
        "edges": [
            {"source": "frontend", "target": "checkoutservice"},
            {"source": "checkoutservice", "target": "paymentservice"},
        ],
    }


def _payload() -> dict:
    return {
        "environmentId": "online-boutique-local",
        "evidenceTier": "experimental",
        "jaegerUrl": "http://localhost:16686",
        "entryService": "frontend",
        "operationName": "POST /cart/checkout",
        "lookback": "1h",
    }


def _generic_payload() -> dict:
    payload = _payload()
    payload.pop("jaegerUrl")
    payload["traceSource"] = {
        "type": "jaeger",
        "endpoint": "http://localhost:16686",
        "authentication": {"type": "none"},
    }
    return payload


def test_session_is_server_managed_and_evidence_honest(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import service

    monkeypatch.setattr(service, "get_live_evidence", lambda *args: _evidence())
    repository = InMemoryAnalysisSessionRepository()
    result = start_workflow_analysis(
        _payload(), actor="operator", repository=repository, now=NOW
    )
    assert result.session_id.startswith("analysis:")
    assert result.workflow_id == "post_cart_checkout"
    assert result.maturity.value == "observed"
    assert result.next_action == "confirm_success_condition"
    assert "participation, not dependency necessity" in result.limitations[0]
    assert repository.get(result.session_id) == result


def test_session_uses_generic_trace_source_contract(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import service

    monkeypatch.setattr(service, "get_live_evidence", lambda *args: _evidence())
    result = start_workflow_analysis(
        _generic_payload(),
        actor="operator",
        repository=InMemoryAnalysisSessionRepository(),
        now=NOW,
    )

    assert result.trace_source.kind.value == "jaeger"
    assert result.trace_source.endpoint == "http://localhost:16686"
    assert result.to_mapping()["traceSource"] == _generic_payload()["traceSource"]


def test_trace_source_persists_secret_reference_without_secret(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import service

    monkeypatch.setattr(service, "get_live_evidence", lambda *args: _evidence())
    monkeypatch.setenv("JAEGER_API_TOKEN", "private-token")
    payload = _generic_payload()
    payload["traceSource"]["authentication"] = {
        "type": "bearer_env",
        "secretEnv": "JAEGER_API_TOKEN",
    }
    result = start_workflow_analysis(
        payload,
        actor="operator",
        repository=InMemoryAnalysisSessionRepository(),
        now=NOW,
    )
    assert result.trace_source.secret_env == "JAEGER_API_TOKEN"
    assert "private-token" not in str(result.to_mapping())


def test_unapproved_jaeger_host_is_rejected_before_network_access(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import service

    called = False

    def fetch(*args):
        nonlocal called
        called = True
        return _evidence()

    monkeypatch.setattr(service, "get_live_evidence", fetch)
    payload = {**_payload(), "jaegerUrl": "http://169.254.169.254/latest/meta-data"}
    repository = InMemoryAnalysisSessionRepository()
    with pytest.raises(ValueError, match="not approved"):
        start_workflow_analysis(
            payload,
            actor="operator",
            repository=repository,
            now=NOW,
        )
    assert called is False


def test_sqlalchemy_analysis_session_round_trip(monkeypatch, tmp_path) -> None:
    from app.engine.workflow_dependency_modeling import service

    monkeypatch.setattr(service, "get_live_evidence", lambda *args: _evidence())
    engine = create_engine(f"sqlite:///{tmp_path / 'analysis.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyAnalysisSessionRepository(engine)
    created = start_workflow_analysis(
        _payload(), actor="operator", repository=repository, now=NOW
    )
    reopened = SqlAlchemyAnalysisSessionRepository(engine)
    assert reopened.get(created.session_id) == created
    assert reopened.list() == [created]


def test_guided_discovery_uses_approved_connection(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import service

    discovered = [
        {
            "workflowId": "post_cart_checkout",
            "operationName": "POST /cart/checkout",
            "rootService": "frontend",
            "traceCount": 12,
            "participatingServices": ["frontend", "checkoutservice"],
            "sampleTraceIds": ["trace-1"],
            "status": "candidate",
        }
    ]
    monkeypatch.setattr(service, "discover_live_workflows", lambda **kwargs: discovered)
    result = discover_analysis_workflows(
        {
            "jaegerUrl": "http://localhost:16686",
            "entryService": "frontend",
            "lookback": "1h",
        }
    )
    assert result == discovered


def test_analysis_session_api(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import analysis_session_service, service

    repository = InMemoryAnalysisSessionRepository()
    monkeypatch.setattr(analysis_session_service, "_default_repository", repository)
    monkeypatch.setattr(service, "get_live_evidence", lambda *args: _evidence())
    monkeypatch.setattr(
        service,
        "discover_live_workflows",
        lambda **kwargs: [
            {
                "workflowId": "post_cart_checkout",
                "operationName": "POST /cart/checkout",
                "rootService": "frontend",
                "traceCount": 12,
                "participatingServices": ["frontend", "checkoutservice"],
                "sampleTraceIds": ["trace-1"],
                "status": "candidate",
            }
        ],
    )
    client = TestClient(app)
    discovery = client.post(
        "/api/workflows/analysis-sessions/discover",
        json={
            "jaegerUrl": "http://localhost:16686",
            "entryService": "frontend",
            "lookback": "1h",
        },
    )
    assert discovery.status_code == 200
    assert discovery.json()[0]["operationName"] == "POST /cart/checkout"
    response = client.post(
        "/api/workflows/analysis-sessions",
        json=_payload(),
        headers={"X-Actor": "operator"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["maturity"] == "observed"
    assert body["evidenceTier"] == "experimental"
    assert body["traceCount"] == 12
    assert client.get("/api/workflows/analysis-sessions").json()[0] == body
    assert client.get(
        f"/api/workflows/analysis-sessions/{body['sessionId']}"
    ).json() == body


def test_analysis_session_http_exceptions_are_documented() -> None:
    schema = TestClient(app).get("/openapi.json").json()
    responses = schema["paths"]["/api/workflows/analysis-sessions"]["post"][
        "responses"
    ]
    assert {"201", "409", "422", "503"} <= responses.keys()
