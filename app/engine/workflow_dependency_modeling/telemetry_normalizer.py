"""Normalize supported telemetry without overstating its evidential strength.

Zipkin v2 and OTLP/Tempo payloads become the existing Jaeger-shaped internal
trace contract.  Logs, network observations, and source models remain explicit
non-causal edge observations and never enter the causal trace parser.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.models import ComponentDataError


_OTLP_KIND = {
    0: "unspecified",
    1: "internal",
    2: "server",
    3: "client",
    4: "producer",
    5: "consumer",
}


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ComponentDataError(f"{field} is required")
    return value.strip()


def _attribute_value(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    for key in (
        "stringValue",
        "boolValue",
        "intValue",
        "doubleValue",
        "bytesValue",
        "arrayValue",
        "kvlistValue",
    ):
        if key in value:
            return value[key]
    return value


def _attributes(values: Any) -> dict[str, Any]:
    if not isinstance(values, list):
        return {}
    return {
        str(item.get("key", "")): _attribute_value(item.get("value"))
        for item in values
        if isinstance(item, Mapping) and item.get("key")
    }


def _jaeger_tag(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        kind = "bool"
    elif isinstance(value, int):
        kind = "int64"
    elif isinstance(value, float):
        kind = "float64"
    else:
        kind = "string"
        value = str(value)
    return {"key": key, "type": kind, "value": value}


def normalize_zipkin_traces(payload: Any) -> list[dict[str, Any]]:
    """Convert Zipkin v2 spans/traces to Component 1's trace contract."""
    if isinstance(payload, Mapping):
        payload = payload.get("data", payload.get("traces"))
    if not isinstance(payload, list):
        raise ComponentDataError("Zipkin payload must be a trace list")
    if not payload:
        return []
    raw_traces = payload if isinstance(payload[0], list) else [payload]
    normalized: list[dict[str, Any]] = []
    for trace_index, raw_trace in enumerate(raw_traces):
        if not isinstance(raw_trace, list) or not raw_trace:
            raise ComponentDataError(f"Zipkin trace {trace_index} must contain spans")
        if any(not isinstance(span, Mapping) for span in raw_trace):
            raise ComponentDataError(f"Zipkin trace {trace_index} contains a non-object span")
        trace_ids = {str(span.get("traceId", "")).strip() for span in raw_trace}
        trace_ids.discard("")
        if len(trace_ids) != 1:
            raise ComponentDataError(
                f"Zipkin trace {trace_index} must contain exactly one traceId"
            )
        trace_id = next(iter(trace_ids))
        service_names = sorted(
            {
                str(span.get("localEndpoint", {}).get("serviceName", "")).strip()
                for span in raw_trace
                if isinstance(span.get("localEndpoint"), Mapping)
            }
            - {""}
        )
        process_by_service = {
            service: f"p{index + 1}" for index, service in enumerate(service_names)
        }
        processes = {
            process_id: {"serviceName": service, "tags": []}
            for service, process_id in process_by_service.items()
        }
        spans: list[dict[str, Any]] = []
        for raw_span in raw_trace:
            endpoint = raw_span.get("localEndpoint", {})
            service = (
                str(endpoint.get("serviceName", "")).strip()
                if isinstance(endpoint, Mapping)
                else ""
            )
            if not service:
                raise ComponentDataError("Every Zipkin span requires localEndpoint.serviceName")
            span_id = _required_text(raw_span.get("id"), "Zipkin span id")
            tags = [
                _jaeger_tag(str(key), value)
                for key, value in dict(raw_span.get("tags") or {}).items()
            ]
            kind = str(raw_span.get("kind", "")).strip().lower()
            if kind:
                tags.append(_jaeger_tag("span.kind", kind))
            if str(dict(raw_span.get("tags") or {}).get("error", "")).lower() in {
                "true",
                "1",
            }:
                tags.append(_jaeger_tag("error", True))
            parent_id = str(raw_span.get("parentId", "")).strip()
            spans.append(
                {
                    "traceID": trace_id,
                    "spanID": span_id,
                    "operationName": str(raw_span.get("name", "")),
                    "references": (
                        [{"refType": "CHILD_OF", "traceID": trace_id, "spanID": parent_id}]
                        if parent_id
                        else []
                    ),
                    "startTime": int(raw_span.get("timestamp") or 0),
                    "duration": int(raw_span.get("duration") or 0),
                    "tags": tags,
                    "processID": process_by_service[service],
                }
            )
        normalized.append({"traceID": trace_id, "spans": spans, "processes": processes})
    return normalized


def _otlp_kind(value: Any) -> str:
    if isinstance(value, int):
        return _OTLP_KIND.get(value, "unspecified")
    text = str(value or "").lower().removeprefix("span_kind_")
    return text or "unspecified"


def _otlp_status(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "UNSET"
    code = value.get("code", 0)
    if isinstance(code, int):
        return {0: "UNSET", 1: "OK", 2: "ERROR"}.get(code, "UNSET")
    return str(code).upper().removeprefix("STATUS_CODE_")


def normalize_otlp_traces(payload: Any) -> list[dict[str, Any]]:
    """Convert OTLP JSON, including Tempo v2 trace responses, to traces."""
    if not isinstance(payload, Mapping):
        raise ComponentDataError("OTLP payload must be an object")
    resource_spans = payload.get("resourceSpans", payload.get("batches"))
    if not isinstance(resource_spans, list):
        raise ComponentDataError("OTLP payload requires resourceSpans or batches")
    grouped: dict[str, dict[str, Any]] = {}
    process_keys: dict[tuple[str, str], str] = {}
    for resource_index, resource_span in enumerate(resource_spans):
        if not isinstance(resource_span, Mapping):
            raise ComponentDataError(f"resourceSpans[{resource_index}] must be an object")
        resource = resource_span.get("resource", {})
        resource_attrs = _attributes(
            resource.get("attributes", []) if isinstance(resource, Mapping) else []
        )
        service = str(resource_attrs.get("service.name", "")).strip()
        if not service:
            raise ComponentDataError(
                f"resourceSpans[{resource_index}] requires resource service.name"
            )
        scope_groups = resource_span.get(
            "scopeSpans", resource_span.get("instrumentationLibrarySpans", [])
        )
        if not isinstance(scope_groups, list):
            raise ComponentDataError(f"resourceSpans[{resource_index}] has invalid scopeSpans")
        for scope_group in scope_groups:
            if not isinstance(scope_group, Mapping):
                continue
            for raw_span in scope_group.get("spans", []):
                if not isinstance(raw_span, Mapping):
                    raise ComponentDataError("OTLP spans must be objects")
                trace_id = _required_text(raw_span.get("traceId"), "OTLP traceId")
                span_id = _required_text(raw_span.get("spanId"), "OTLP spanId")
                trace = grouped.setdefault(
                    trace_id, {"traceID": trace_id, "spans": [], "processes": {}}
                )
                process_key = (trace_id, service)
                process_id = process_keys.setdefault(
                    process_key, f"p{len(trace['processes']) + 1}"
                )
                trace["processes"].setdefault(
                    process_id,
                    {
                        "serviceName": service,
                        "tags": [
                            _jaeger_tag(key, value)
                            for key, value in resource_attrs.items()
                            if key != "service.name"
                        ],
                    },
                )
                start_ns = int(raw_span.get("startTimeUnixNano") or 0)
                end_ns = int(raw_span.get("endTimeUnixNano") or start_ns)
                status = _otlp_status(raw_span.get("status"))
                tags = [
                    _jaeger_tag(key, value)
                    for key, value in _attributes(raw_span.get("attributes", [])).items()
                ]
                tags.extend(
                    [
                        _jaeger_tag("span.kind", _otlp_kind(raw_span.get("kind"))),
                        _jaeger_tag("otel.status_code", status),
                    ]
                )
                if status == "ERROR":
                    tags.append(_jaeger_tag("error", True))
                parent_id = str(raw_span.get("parentSpanId", "")).strip()
                references = (
                    [{"refType": "CHILD_OF", "traceID": trace_id, "spanID": parent_id}]
                    if parent_id
                    else []
                )
                raw_links = raw_span.get("links", [])
                if not isinstance(raw_links, list):
                    raise ComponentDataError("OTLP span links must be a list")
                for raw_link in raw_links:
                    if not isinstance(raw_link, Mapping):
                        raise ComponentDataError("OTLP span links must be objects")
                    linked_trace_id = _required_text(
                        raw_link.get("traceId"), "OTLP link traceId"
                    )
                    linked_span_id = _required_text(
                        raw_link.get("spanId"), "OTLP link spanId"
                    )
                    references.append(
                        {
                            "refType": "SPAN_LINK",
                            "traceID": linked_trace_id,
                            "spanID": linked_span_id,
                        }
                    )
                trace["spans"].append(
                    {
                        "traceID": trace_id,
                        "spanID": span_id,
                        "operationName": str(raw_span.get("name", "")),
                        "references": references,
                        "startTime": start_ns // 1_000,
                        "duration": max(end_ns - start_ns, 0) // 1_000,
                        "tags": tags,
                        "processID": process_id,
                    }
                )
    return list(grouped.values())


@dataclass(frozen=True)
class NonCausalEdgeEvidence:
    source: str
    target: str
    evidence_type: str
    observation_count: int
    correlation_ids: tuple[str, ...]
    first_observed_at: str | None
    last_observed_at: str | None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "evidenceType": self.evidence_type,
            "causal": False,
            "observationCount": self.observation_count,
            "correlationIds": list(self.correlation_ids),
            "firstObservedAt": self.first_observed_at,
            "lastObservedAt": self.last_observed_at,
        }


def _timestamp(value: Any, field: str) -> str:
    text = _required_text(value, field)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as err:
        raise ComponentDataError(f"{field} must be an ISO-8601 timestamp") from err
    return text


def normalize_non_causal_edges(
    payload: Any, *, source_type: str
) -> list[NonCausalEdgeEvidence]:
    """Aggregate log/network/source relationships as non-causal evidence."""
    if isinstance(payload, Mapping):
        payload = payload.get("observations", payload.get("entries", payload.get("edges")))
    if not isinstance(payload, list):
        raise ComponentDataError(f"{source_type} payload must contain an observation list")
    grouped: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for index, item in enumerate(payload):
        if not isinstance(item, Mapping):
            raise ComponentDataError(f"observation {index} must be an object")
        source = _required_text(
            item.get("sourceService", item.get("source")),
            f"observation {index} sourceService",
        )
        target = _required_text(
            item.get("targetService", item.get("target")),
            f"observation {index} targetService",
        )
        if source_type == "source_model":
            timestamp = str(item.get("timestamp", "")).strip()
        else:
            timestamp = _timestamp(item.get("timestamp"), f"observation {index} timestamp")
        correlation = str(
            item.get("correlationId", item.get("requestId", ""))
        ).strip()
        if source_type == "correlated_logs" and not correlation:
            raise ComponentDataError(
                f"observation {index} correlationId is required for correlated logs"
            )
        grouped[(source, target)].append((timestamp, correlation))
    results: list[NonCausalEdgeEvidence] = []
    for (source, target), records in sorted(grouped.items()):
        timestamps = sorted(value for value, _ in records if value)
        correlations = tuple(sorted({value for _, value in records if value}))
        results.append(
            NonCausalEdgeEvidence(
                source=source,
                target=target,
                evidence_type={
                    "correlated_logs": "LOG_CORRELATED",
                    "network_observations": "NETWORK_OBSERVED",
                    "source_model": "SOURCE_CANDIDATE",
                }[source_type],
                observation_count=len(records),
                correlation_ids=correlations,
                first_observed_at=timestamps[0] if timestamps else None,
                last_observed_at=timestamps[-1] if timestamps else None,
            )
        )
    return results


def normalize_telemetry_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize an offline payload and return its explicit evidence contract."""
    if not isinstance(payload, Mapping):
        raise ComponentDataError("telemetry normalization payload must be an object")
    source_type = str(payload.get("sourceType", "")).strip()
    raw = payload.get("payload")
    if source_type in {"offline_jaeger", "zipkin", "tempo", "vendor_tracing"}:
        if source_type == "offline_jaeger":
            value = raw.get("data") if isinstance(raw, Mapping) else raw
            if not isinstance(value, list) or any(
                not isinstance(item, Mapping) for item in value
            ):
                raise ComponentDataError(
                    "offline_jaeger payload must be a trace list or Jaeger data response"
                )
            traces = [dict(item) for item in value]
        elif source_type == "zipkin":
            traces = normalize_zipkin_traces(raw)
        else:
            traces = normalize_otlp_traces(raw)
        return {
            "sourceType": source_type,
            "evidenceLabel": "TRACE_CAUSAL",
            "causal": True,
            "traceCount": len(traces),
            "traces": traces,
        }
    if source_type in {"correlated_logs", "network_observations", "source_model"}:
        edges = normalize_non_causal_edges(raw, source_type=source_type)
        return {
            "sourceType": source_type,
            "evidenceLabel": edges[0].evidence_type if edges else {
                "correlated_logs": "LOG_CORRELATED",
                "network_observations": "NETWORK_OBSERVED",
                "source_model": "SOURCE_CANDIDATE",
            }[source_type],
            "causal": False,
            "edgeCount": len(edges),
            "edges": [edge.to_mapping() for edge in edges],
        }
    raise ComponentDataError(
        "sourceType must be offline_jaeger, zipkin, tempo, vendor_tracing, correlated_logs, "
        "network_observations, or source_model"
    )


def inspect_telemetry_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a bounded candidate-graph inspection from normalized evidence."""
    normalized = normalize_telemetry_payload(payload)
    if not normalized["causal"]:
        return normalized

    from app.engine.workflow_dependency_modeling.auto_discovery import (
        discover_root_workflows,
    )
    from app.engine.workflow_dependency_modeling.interaction_extractor import (
        aggregate_edges,
        aggregate_linked_edges,
        extract_interactions,
    )
    from app.engine.workflow_dependency_modeling.trace_reader import parse_spans
    from app.engine.workflow_dependency_modeling.trace_validator import (
        eligible_traces,
        validate_trace_quality,
    )

    traces = normalized.pop("traces")
    quality = validate_trace_quality(traces)
    eligible = eligible_traces(traces, quality)
    spans = parse_spans(eligible)
    interactions = extract_interactions(spans)
    edges = aggregate_edges(interactions)
    linked_edges = aggregate_linked_edges(interactions)
    workflows = discover_root_workflows(eligible)
    normalized.update(
        {
            "eligibleTraceCount": len(eligible),
            "observedServices": sorted({span.service_name for span in spans}),
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "occurrenceCount": edge.occurrence_count,
                    "frequency": edge.frequency,
                    "totalCallCount": edge.total_call_count,
                    "maxCallsPerTrace": edge.max_calls_per_trace,
                    "repeatedTraceCount": edge.repeated_trace_count,
                }
                for edge in edges
            ],
            "linkedEdges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "evidenceType": "SPAN_LINK",
                    "occurrenceCount": edge.occurrence_count,
                    "frequency": edge.frequency,
                }
                for edge in linked_edges
            ],
            "discoveredWorkflows": [
                {
                    "workflowId": workflow.workflow_id,
                    "operationName": workflow.operation_name,
                    "rootService": workflow.root_service,
                    "traceCount": workflow.trace_count,
                    "participatingServices": workflow.participating_services,
                }
                for workflow in workflows
            ],
            "traceQuality": quality.to_mapping(),
        }
    )
    return normalized
