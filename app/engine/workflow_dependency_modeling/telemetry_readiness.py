"""Source-agnostic telemetry intake and evidence-readiness assessment.

This module does not convert weak evidence into distributed traces.  It records
what a project can actually provide and gates downstream claims accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.engine.workflow_dependency_modeling.trace_validator import (
    validate_trace_quality,
)
from app.engine.workflow_dependency_modeling.telemetry_normalizer import (
    normalize_telemetry_payload,
    normalize_otlp_traces,
)
from app.engine.workflow_dependency_modeling.trace_source import (
    TraceSourceConfiguration,
    create_trace_source,
)
from app.integrations.jaeger.client import JaegerClient


class TelemetrySourceType(str, Enum):
    JAEGER = "jaeger"
    OFFLINE_JAEGER = "offline_jaeger"
    ZIPKIN = "zipkin"
    TEMPO = "tempo"
    VENDOR_TRACING = "vendor_tracing"
    CORRELATED_LOGS = "correlated_logs"
    NETWORK_OBSERVATIONS = "network_observations"
    SOURCE_MODEL = "source_model"
    NONE = "none"


class EvidenceLevel(IntEnum):
    NONE = 0
    SOURCE_CANDIDATE = 1
    NETWORK_OBSERVED = 2
    LOG_CORRELATED = 3
    TRACE_CAUSAL = 4
    INTERVENTION_VALIDATED = 5


_LEVEL_NAMES = {
    EvidenceLevel.NONE: "NO_EVIDENCE",
    EvidenceLevel.SOURCE_CANDIDATE: "SOURCE_CANDIDATE",
    EvidenceLevel.NETWORK_OBSERVED: "NETWORK_OBSERVED",
    EvidenceLevel.LOG_CORRELATED: "LOG_CORRELATED",
    EvidenceLevel.TRACE_CAUSAL: "TRACE_CAUSAL",
    EvidenceLevel.INTERVENTION_VALIDATED: "INTERVENTION_VALIDATED",
}

_ADAPTERS = {
    TelemetrySourceType.JAEGER: "available",
    TelemetrySourceType.OFFLINE_JAEGER: "available",
    TelemetrySourceType.ZIPKIN: "available",
    TelemetrySourceType.TEMPO: "available",
    TelemetrySourceType.VENDOR_TRACING: "offline_otlp_available",
    TelemetrySourceType.CORRELATED_LOGS: "non_causal_normalizer_available",
    TelemetrySourceType.NETWORK_OBSERVATIONS: "non_causal_normalizer_available",
    TelemetrySourceType.SOURCE_MODEL: "non_causal_normalizer_available",
    TelemetrySourceType.NONE: "not_applicable",
}


def _boolean(data: Mapping[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise ComponentDataError(f"telemetry readiness '{key}' must be boolean")
    return value


def _endpoint(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ComponentDataError("telemetry readiness endpoint is required")
    endpoint = value.strip().rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ComponentDataError("telemetry endpoint must be an HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ComponentDataError("telemetry endpoint must not contain credentials")
    return endpoint


def _offline_traces(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping) and isinstance(value.get("data"), list):
        value = value["data"]
    if not isinstance(value, list) or not value:
        raise ComponentDataError(
            "offline_jaeger readiness requires a non-empty traces list or Jaeger response"
        )
    if any(not isinstance(item, Mapping) for item in value):
        raise ComponentDataError("offline Jaeger traces must be JSON objects")
    return [dict(item) for item in value]


@dataclass(frozen=True)
class TelemetryReadinessResult:
    source_type: TelemetrySourceType
    adapter_status: str
    evidence_level: EvidenceLevel
    connected: bool
    ready_for_candidate_discovery: bool
    ready_for_causal_graph: bool
    ready_for_validated_classification: bool
    observed_services: tuple[str, ...]
    blockers: tuple[str, ...]
    next_actions: tuple[str, ...]
    diagnostics: Mapping[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "sourceType": self.source_type.value,
            "adapterStatus": self.adapter_status,
            "evidenceLevel": int(self.evidence_level),
            "evidenceLabel": _LEVEL_NAMES[self.evidence_level],
            "connected": self.connected,
            "readyForCandidateDiscovery": self.ready_for_candidate_discovery,
            "readyForCausalGraph": self.ready_for_causal_graph,
            "readyForValidatedClassification": (
                self.ready_for_validated_classification
            ),
            "observedServices": list(self.observed_services),
            "blockers": list(self.blockers),
            "nextActions": list(self.next_actions),
            "diagnostics": dict(self.diagnostics),
        }


def _result(
    source_type: TelemetrySourceType,
    level: EvidenceLevel,
    *,
    connected: bool = False,
    candidate: bool = False,
    causal: bool = False,
    services: Sequence[str] = (),
    blockers: Sequence[str] = (),
    actions: Sequence[str] = (),
    diagnostics: Mapping[str, Any] | None = None,
) -> TelemetryReadinessResult:
    return TelemetryReadinessResult(
        source_type=source_type,
        adapter_status=_ADAPTERS[source_type],
        evidence_level=level,
        connected=connected,
        ready_for_candidate_discovery=candidate,
        ready_for_causal_graph=causal,
        ready_for_validated_classification=False,
        observed_services=tuple(sorted(set(services))),
        blockers=tuple(blockers),
        next_actions=tuple(actions),
        diagnostics=diagnostics or {},
    )


def _assess_jaeger(
    payload: Mapping[str, Any],
    client_factory: Callable[[str], JaegerClient],
) -> TelemetryReadinessResult:
    endpoint = _endpoint(payload.get("endpoint"))
    entry_service = str(payload.get("entryService", "")).strip()
    try:
        services = client_factory(endpoint).get_services()
    except (ConnectionError, OSError, ValueError) as err:
        return _result(
            TelemetrySourceType.JAEGER,
            EvidenceLevel.NONE,
            blockers=("The Jaeger Query API could not be reached.",),
            actions=("Verify the URL, network route, TLS and read credentials.",),
            diagnostics={"endpoint": endpoint, "connectionError": str(err)},
        )
    blockers: list[str] = []
    actions = [
        "Register a workflow operation and business-success condition.",
        "Collect repeated exact-correlated baseline traces before interventions.",
    ]
    if not services:
        blockers.append("Jaeger is reachable but contains no service telemetry.")
        actions.insert(0, "Configure applications or a collector to export spans.")
    if entry_service and entry_service not in services:
        blockers.append(
            f"Entry service '{entry_service}' is not present in Jaeger."
        )
        actions.insert(0, "Check service.name and the selected time window.")
    ready = not blockers
    return _result(
        TelemetrySourceType.JAEGER,
        EvidenceLevel.TRACE_CAUSAL if ready else EvidenceLevel.NONE,
        connected=True,
        candidate=ready,
        causal=ready,
        services=services,
        blockers=blockers,
        actions=actions,
        diagnostics={"endpoint": endpoint, "serviceCount": len(services)},
    )


def _assess_offline(payload: Mapping[str, Any]) -> TelemetryReadinessResult:
    traces = _offline_traces(payload.get("traces"))
    quality = validate_trace_quality(traces)
    valid_count = len(quality.eligible_trace_ids)
    blockers = (
        ()
        if valid_count
        else ("The offline export contains no structurally valid traces.",)
    )
    return _result(
        TelemetrySourceType.OFFLINE_JAEGER,
        EvidenceLevel.TRACE_CAUSAL if valid_count else EvidenceLevel.NONE,
        connected=True,
        candidate=bool(valid_count),
        causal=bool(valid_count),
        blockers=blockers,
        actions=(
            "Register the workflow success condition and evidence provenance.",
            "Use controlled outcome records before classifying necessity.",
        ),
        diagnostics=quality.to_mapping(),
    )


def _assess_query_backend(
    source_type: TelemetrySourceType,
    payload: Mapping[str, Any],
    source_factory: Callable[[str], Any],
) -> TelemetryReadinessResult:
    endpoint = _endpoint(payload.get("endpoint"))
    entry_service = str(payload.get("entryService", "")).strip()
    try:
        services = source_factory(endpoint).get_services()
    except (ConnectionError, OSError, ValueError) as err:
        return _result(
            source_type,
            EvidenceLevel.NONE,
            blockers=(f"The {source_type.value} query API could not be reached.",),
            actions=("Verify the query URL, network route, TLS and read credentials.",),
            diagnostics={"endpoint": endpoint, "connectionError": str(err)},
        )
    blockers: list[str] = []
    if not services:
        blockers.append(
            f"{source_type.value.title()} is reachable but contains no service telemetry."
        )
    if entry_service and entry_service not in services:
        blockers.append(f"Entry service '{entry_service}' is not present in {source_type.value}.")
    ready = not blockers
    return _result(
        source_type,
        EvidenceLevel.TRACE_CAUSAL if ready else EvidenceLevel.NONE,
        connected=True,
        candidate=ready,
        causal=ready,
        services=services,
        blockers=blockers,
        actions=(
            "Register a workflow operation and business-success condition.",
            "Collect repeated exact-correlated baseline traces before interventions.",
        ),
        diagnostics={"endpoint": endpoint, "serviceCount": len(services)},
    )


def _assess_vendor_otlp(payload: Mapping[str, Any]) -> TelemetryReadinessResult:
    raw_payload = payload.get("traces")
    if raw_payload is None:
        return _result(
            TelemetrySourceType.VENDOR_TRACING,
            EvidenceLevel.NONE,
            blockers=("Vendor tracing requires an exported OTLP JSON payload.",),
            actions=(
                "Export OTLP JSON from the vendor, or add its authenticated query adapter.",
            ),
        )
    traces = normalize_otlp_traces(raw_payload)
    quality = validate_trace_quality(traces)
    valid_count = len(quality.eligible_trace_ids)
    return _result(
        TelemetrySourceType.VENDOR_TRACING,
        EvidenceLevel.TRACE_CAUSAL if valid_count else EvidenceLevel.NONE,
        connected=True,
        candidate=bool(valid_count),
        causal=bool(valid_count),
        blockers=(
            () if valid_count else ("The OTLP export contains no structurally valid traces.",)
        ),
        actions=("Record the vendor, export query, time window, and immutable checksum.",),
        diagnostics=quality.to_mapping(),
    )


def _assess_normalized_export(
    source_type: TelemetrySourceType, raw_payload: Any
) -> TelemetryReadinessResult:
    normalized = normalize_telemetry_payload(
        {"sourceType": source_type.value, "payload": raw_payload}
    )
    traces = normalized["traces"]
    quality = validate_trace_quality(traces)
    valid_count = len(quality.eligible_trace_ids)
    return _result(
        source_type,
        EvidenceLevel.TRACE_CAUSAL if valid_count else EvidenceLevel.NONE,
        connected=True,
        candidate=bool(valid_count),
        causal=bool(valid_count),
        blockers=(
            () if valid_count else ("The trace export contains no structurally valid traces.",)
        ),
        actions=("Record the export query, time window, and immutable checksum.",),
        diagnostics=quality.to_mapping(),
    )


def _assess_declared_source(
    source_type: TelemetrySourceType, payload: Mapping[str, Any]
) -> TelemetryReadinessResult:
    identities = _boolean(payload, "hasServiceIdentities")
    timestamps = _boolean(payload, "hasTimestamps")
    correlation = _boolean(payload, "hasCorrelationIds")
    parents = _boolean(payload, "hasParentRelationships")
    outcomes = _boolean(payload, "hasOutcomeStatus")
    capabilities = {
        "hasServiceIdentities": identities,
        "hasTimestamps": timestamps,
        "hasCorrelationIds": correlation,
        "hasParentRelationships": parents,
        "hasOutcomeStatus": outcomes,
    }
    if source_type is TelemetrySourceType.CORRELATED_LOGS:
        missing = [
            name
            for name, present in {
                "service identities": identities,
                "timestamps": timestamps,
                "correlation IDs": correlation,
                "outcome status": outcomes,
            }.items()
            if not present
        ]
        ready = not missing
        return _result(
            source_type,
            EvidenceLevel.LOG_CORRELATED if ready else EvidenceLevel.NONE,
            connected=ready,
            candidate=ready,
            causal=False,
            blockers=(
                ("Correlated logs are missing: " + ", ".join(missing) + ".",)
                if missing
                else ()
            ),
            actions=(
                "Import observations through the non-causal log normalizer.",
                "Use distributed traces for causal parent-child reconstruction.",
            ),
            diagnostics=capabilities,
        )
    if source_type is TelemetrySourceType.NETWORK_OBSERVATIONS:
        missing = [
            name
            for name, present in {
                "service identities": identities,
                "timestamps": timestamps,
            }.items()
            if not present
        ]
        ready = not missing
        return _result(
            source_type,
            EvidenceLevel.NETWORK_OBSERVED if ready else EvidenceLevel.NONE,
            connected=ready,
            candidate=ready,
            causal=False,
            blockers=(
                ("Network observations are missing: " + ", ".join(missing) + ".",)
                if missing
                else ()
            ),
            actions=(
                "Treat recovered edges as communication observations, not causal traces.",
            ),
            diagnostics=capabilities,
        )
    if source_type is TelemetrySourceType.SOURCE_MODEL:
        return _result(
            source_type,
            EvidenceLevel.SOURCE_CANDIDATE,
            connected=True,
            candidate=True,
            causal=False,
            actions=(
                "Label results as source-expected candidates until runtime evidence exists.",
            ),
            diagnostics=capabilities,
        )
    return _result(
        source_type,
        EvidenceLevel.NONE,
        blockers=("No workflow-correlated runtime evidence is available.",),
        actions=(
            "Connect a tracing backend, import an offline trace, or enable correlation IDs.",
        ),
        diagnostics=capabilities,
    )


def assess_telemetry_readiness(
    payload: Mapping[str, Any],
    *,
    jaeger_client_factory: Callable[[str], JaegerClient] | None = None,
    zipkin_source_factory: Callable[[str], Any] | None = None,
    tempo_source_factory: Callable[[str], Any] | None = None,
) -> TelemetryReadinessResult:
    """Assess what claims Component 1 may make from the declared source."""
    if not isinstance(payload, Mapping):
        raise ComponentDataError("telemetry readiness payload must be an object")
    try:
        source_type = TelemetrySourceType(payload.get("sourceType"))
    except ValueError as err:
        allowed = ", ".join(item.value for item in TelemetrySourceType)
        raise ComponentDataError(f"sourceType must be one of: {allowed}") from err
    if source_type is TelemetrySourceType.JAEGER:
        factory = jaeger_client_factory or (
            lambda endpoint: create_trace_source(
                TraceSourceConfiguration.from_mapping(
                    {
                        "type": "jaeger",
                        "endpoint": endpoint,
                        "authentication": payload.get("authentication", {"type": "none"}),
                    }
                )
            )
        )
        return _assess_jaeger(payload, factory)
    if source_type is TelemetrySourceType.OFFLINE_JAEGER:
        return _assess_offline(payload)
    if source_type is TelemetrySourceType.ZIPKIN:
        if payload.get("traces") is not None:
            return _assess_normalized_export(source_type, payload["traces"])
        return _assess_query_backend(
            source_type,
            payload,
            zipkin_source_factory or (
                lambda endpoint: create_trace_source(
                    TraceSourceConfiguration.from_mapping(
                        {
                            "type": "zipkin",
                            "endpoint": endpoint,
                            "authentication": payload.get("authentication", {"type": "none"}),
                        }
                    )
                )
            ),
        )
    if source_type is TelemetrySourceType.TEMPO:
        if payload.get("traces") is not None:
            return _assess_normalized_export(source_type, payload["traces"])
        return _assess_query_backend(
            source_type,
            payload,
            tempo_source_factory or (
                lambda endpoint: create_trace_source(
                    TraceSourceConfiguration.from_mapping(
                        {
                            "type": "tempo",
                            "endpoint": endpoint,
                            "authentication": payload.get("authentication", {"type": "none"}),
                        }
                    )
                )
            ),
        )
    if source_type is TelemetrySourceType.VENDOR_TRACING:
        return _assess_vendor_otlp(payload)
    return _assess_declared_source(source_type, payload)


def telemetry_source_capabilities() -> list[dict[str, Any]]:
    """Return truthful adapter availability for onboarding UIs."""
    levels = {
        TelemetrySourceType.JAEGER: EvidenceLevel.TRACE_CAUSAL,
        TelemetrySourceType.OFFLINE_JAEGER: EvidenceLevel.TRACE_CAUSAL,
        TelemetrySourceType.ZIPKIN: EvidenceLevel.TRACE_CAUSAL,
        TelemetrySourceType.TEMPO: EvidenceLevel.TRACE_CAUSAL,
        TelemetrySourceType.VENDOR_TRACING: EvidenceLevel.TRACE_CAUSAL,
        TelemetrySourceType.CORRELATED_LOGS: EvidenceLevel.LOG_CORRELATED,
        TelemetrySourceType.NETWORK_OBSERVATIONS: EvidenceLevel.NETWORK_OBSERVED,
        TelemetrySourceType.SOURCE_MODEL: EvidenceLevel.SOURCE_CANDIDATE,
        TelemetrySourceType.NONE: EvidenceLevel.NONE,
    }
    return [
        {
            "sourceType": source_type.value,
            "adapterStatus": _ADAPTERS[source_type],
            "maximumEvidenceLevel": int(levels[source_type]),
            "maximumEvidenceLabel": _LEVEL_NAMES[levels[source_type]],
        }
        for source_type in TelemetrySourceType
    ]
