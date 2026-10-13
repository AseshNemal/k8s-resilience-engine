"""Evidence-level gates for real-project telemetry onboarding."""

from app.engine.workflow_dependency_modeling.telemetry_readiness import (
    EvidenceLevel,
    assess_telemetry_readiness,
    telemetry_source_capabilities,
)
from app.engine.workflow_dependency_modeling.trace_reader import (
    load_traces_from_file,
)


class StubJaegerClient:
    def __init__(self, services: list[str] | None = None) -> None:
        self.services = services or []

    def get_services(self) -> list[str]:
        return self.services


def test_live_jaeger_is_ready_for_causal_graph_but_not_classification() -> None:
    result = assess_telemetry_readiness(
        {
            "sourceType": "jaeger",
            "endpoint": "http://jaeger.test:16686",
            "entryService": "frontend",
        },
        jaeger_client_factory=lambda _: StubJaegerClient(
            ["frontend", "checkoutservice"]
        ),
    )

    assert result.evidence_level is EvidenceLevel.TRACE_CAUSAL
    assert result.ready_for_candidate_discovery is True
    assert result.ready_for_causal_graph is True
    assert result.ready_for_validated_classification is False
    assert result.observed_services == ("checkoutservice", "frontend")


def test_missing_entry_service_blocks_live_trace_readiness() -> None:
    result = assess_telemetry_readiness(
        {
            "sourceType": "jaeger",
            "endpoint": "http://jaeger.test:16686",
            "entryService": "orders-api",
        },
        jaeger_client_factory=lambda _: StubJaegerClient(["frontend"]),
    )

    assert result.connected is True
    assert result.ready_for_causal_graph is False
    assert "orders-api" in result.blockers[0]


def test_offline_jaeger_export_uses_the_same_quality_gate() -> None:
    traces = load_traces_from_file("sample_data/traces/checkout_traces.json")
    result = assess_telemetry_readiness(
        {"sourceType": "offline_jaeger", "traces": traces}
    )

    assert result.evidence_level is EvidenceLevel.TRACE_CAUSAL
    assert result.ready_for_causal_graph is True
    assert result.diagnostics["validCount"] == len(traces)


def test_correlated_logs_and_network_flows_never_claim_causal_graphs() -> None:
    logs = assess_telemetry_readiness(
        {
            "sourceType": "correlated_logs",
            "hasServiceIdentities": True,
            "hasTimestamps": True,
            "hasCorrelationIds": True,
            "hasOutcomeStatus": True,
        }
    )
    network = assess_telemetry_readiness(
        {
            "sourceType": "network_observations",
            "hasServiceIdentities": True,
            "hasTimestamps": True,
        }
    )

    assert logs.evidence_level is EvidenceLevel.LOG_CORRELATED
    assert logs.ready_for_candidate_discovery is True
    assert logs.ready_for_causal_graph is False
    assert network.evidence_level is EvidenceLevel.NETWORK_OBSERVED
    assert network.ready_for_causal_graph is False


def test_source_model_and_no_telemetry_have_explicit_limits() -> None:
    source = assess_telemetry_readiness({"sourceType": "source_model"})
    none = assess_telemetry_readiness({"sourceType": "none"})

    assert source.evidence_level is EvidenceLevel.SOURCE_CANDIDATE
    assert source.ready_for_candidate_discovery is True
    assert source.ready_for_causal_graph is False
    assert none.evidence_level is EvidenceLevel.NONE
    assert none.ready_for_candidate_discovery is False
    assert none.blockers


def test_zipkin_and_tempo_adapters_are_available_and_connection_gated() -> None:
    capabilities = {
        item["sourceType"]: item for item in telemetry_source_capabilities()
    }
    assert capabilities["jaeger"]["adapterStatus"] == "available"
    assert capabilities["offline_jaeger"]["adapterStatus"] == "available"
    assert capabilities["zipkin"]["adapterStatus"] == "available"
    assert capabilities["tempo"]["adapterStatus"] == "available"

    result = assess_telemetry_readiness(
        {
            "sourceType": "zipkin",
            "endpoint": "http://zipkin.test:9411",
            "entryService": "frontend",
        },
        zipkin_source_factory=lambda _: StubJaegerClient(["frontend", "cartservice"]),
    )
    assert result.ready_for_candidate_discovery is True
    assert result.ready_for_causal_graph is True
    assert result.observed_services == ("cartservice", "frontend")


def test_vendor_backend_requires_otlp_export_not_capability_checkboxes() -> None:
    result = assess_telemetry_readiness({"sourceType": "vendor_tracing"})

    assert result.ready_for_candidate_discovery is False
    assert "OTLP" in result.blockers[0]


def test_zipkin_export_can_be_assessed_without_live_query_url() -> None:
    trace_id = "0123456789abcdef0123456789abcdef"
    result = assess_telemetry_readiness(
        {
            "sourceType": "zipkin",
            "traces": [
                [
                    {
                        "traceId": trace_id,
                        "id": "1111111111111111",
                        "name": "checkout",
                        "localEndpoint": {"serviceName": "frontend"},
                    }
                ]
            ],
        }
    )

    assert result.connected is True
    assert result.ready_for_causal_graph is True
    assert result.diagnostics["validCount"] == 1
