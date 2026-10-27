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
    list_analysis_services,
    start_workflow_analysis,
)
from app.main import app
from app.shared.persistence.models import Base


NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def test_opt_in_boutique_template_contains_setup_and_measured_success():
    response = TestClient(app).get('/api/workflows/execution-templates/online-boutique-checkout')
    assert response.status_code == 200
    template = response.json()
    assert template['measuredStep'] == 'checkout'
    assert [step['name'] for step in template['steps']] == ['view_product', 'add_to_cart', 'checkout']
    assert template['steps'][-1]['responseContains'] == ['Your order is complete!']
    assert template['successCondition']['operationName'] == 'POST /cart/checkout'
    assert int(template['initialVariables']['expiration_year']) > datetime.now(timezone.utc).year


@pytest.mark.parametrize("backend", ["jaeger", "zipkin", "tempo"])
def test_service_choices_come_from_selected_source_without_manual_identity(monkeypatch, backend):
    from app.engine.workflow_dependency_modeling import analysis_session_service

    configurations = []

    class Source:
        def get_services(self):
            return ["payments", "gateway", "payments", ""]

    def source(configuration):
        configurations.append(configuration)
        return Source()

    monkeypatch.setattr(analysis_session_service, "create_trace_source", source)
    result = list_analysis_services({"traceSource": {
        "type": backend,
        "endpoint": "http://localhost:16686",
        "authentication": {"type": "none"},
    }})
    assert result == ["gateway", "payments"]
    assert configurations[0].kind.value == backend


def test_service_listing_rejects_unapproved_connection_before_creating_adapter(monkeypatch):
    from app.engine.workflow_dependency_modeling import analysis_session_service

    def unexpected_adapter(*args):
        pytest.fail("Unapproved connection reached adapter creation")

    monkeypatch.setattr(analysis_session_service, "create_trace_source", unexpected_adapter)
    response = TestClient(app).post(
        "/api/workflows/analysis-sessions/services",
        json={"jaegerUrl": "http://169.254.169.254/latest/meta-data"},
    )
    assert response.status_code == 422
    assert "not approved" in response.json()["detail"]


def test_service_listing_api_distinguishes_empty_source_from_connection_failure(monkeypatch):
    from app.engine.workflow_dependency_modeling import analysis_session_service

    class Source:
        fail = False

        def get_services(self):
            if self.fail:
                raise ConnectionError("Trace query unavailable")
            return []

    source = Source()
    monkeypatch.setattr(analysis_session_service, "create_trace_source", lambda configuration: source)
    client = TestClient(app)
    payload = {"jaegerUrl": "http://localhost:16686"}
    response = client.post("/api/workflows/analysis-sessions/services", json=payload)
    assert response.status_code == 200
    assert response.json() == []
    source.fail = True
    response = client.post("/api/workflows/analysis-sessions/services", json=payload)
    assert response.status_code == 503
    assert "Trace query unavailable" in response.json()["detail"]


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
        "traceQuality": {
            "totalCount": 12,
            "validCount": 12,
            "partialCount": 0,
            "invalidCount": 0,
            "excludedCount": 0,
            "results": [],
        },
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

    monkeypatch.setattr(service, "get_live_evidence", lambda *args, **kwargs: _evidence())
    repository = InMemoryAnalysisSessionRepository()
    result = start_workflow_analysis(
        _payload(), actor="operator", repository=repository, now=NOW
    )
    assert result.session_id.startswith("analysis:")
    assert result.workflow_id == "post_cart_checkout"
    assert result.maturity.value == "observed"
    assert result.next_action == "confirm_success_condition"
    assert result.trace_quality["validCount"] == 12
    assert "participation, not dependency necessity" in result.limitations[0]
    assert repository.get(result.session_id) == result


def test_incomplete_trace_evidence_requires_instrumentation_repair(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import service

    evidence = _evidence()
    evidence["traceQuality"] = {
        "totalCount": 12,
        "validCount": 0,
        "partialCount": 12,
        "invalidCount": 0,
        "excludedCount": 12,
        "results": [],
    }
    monkeypatch.setattr(service, "get_live_evidence", lambda *args, **kwargs: evidence)

    result = start_workflow_analysis(
        _payload(),
        actor="operator",
        repository=InMemoryAnalysisSessionRepository(),
        now=NOW,
    )

    assert result.next_action == "repair_trace_instrumentation"
    assert "incomplete" in result.limitations[-1]
    assert "Repair trace" in result.required_actions[0]


def test_session_uses_generic_trace_source_contract(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import service

    monkeypatch.setattr(service, "get_live_evidence", lambda *args, **kwargs: _evidence())
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

    monkeypatch.setattr(service, "get_live_evidence", lambda *args, **kwargs: _evidence())
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

    def fetch(*args, **kwargs):
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

    monkeypatch.setattr(service, "get_live_evidence", lambda *args, **kwargs: _evidence())
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
    monkeypatch.setattr(service, "get_live_evidence", lambda *args, **kwargs: _evidence())
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
