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


def test_unapproved_jaeger_host_is_rejected_before_network_access(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import service

    called = False

    def fetch(*args):
        nonlocal called
        called = True
        return _evidence()

    monkeypatch.setattr(service, "get_live_evidence", fetch)
    payload = {**_payload(), "jaegerUrl": "http://169.254.169.254/latest/meta-data"}
    with pytest.raises(ValueError, match="not approved"):
        start_workflow_analysis(
            payload,
            actor="operator",
            repository=InMemoryAnalysisSessionRepository(),
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


def test_analysis_session_api(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import analysis_session_service, service

    repository = InMemoryAnalysisSessionRepository()
    monkeypatch.setattr(analysis_session_service, "_default_repository", repository)
    monkeypatch.setattr(service, "get_live_evidence", lambda *args: _evidence())
    client = TestClient(app)
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
