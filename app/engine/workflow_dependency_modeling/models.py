"""Internal Component 1 models with explicit research-data boundaries.

These models deliberately do not extend the shared ``WorkflowGraph`` contract.
Scenarios describe what should be exercised, evidence identifies what was
observed, and references are available only to evaluation code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, TypeVar


class ComponentDataError(ValueError):
    """Raised when Component 1 data violates an internal contract."""


class SuccessProtocol(str, Enum):
    HTTP = "http"
    GRPC = "grpc"
    SPAN = "span"


class ScenarioLifecycle(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class ScenarioSource(str, Enum):
    MANUAL = "manual"
    DISCOVERED = "discovered"


GRPC_STATUS_CODES = frozenset(
    {
        "OK",
        "CANCELLED",
        "UNKNOWN",
        "INVALID_ARGUMENT",
        "DEADLINE_EXCEEDED",
        "NOT_FOUND",
        "ALREADY_EXISTS",
        "PERMISSION_DENIED",
        "RESOURCE_EXHAUSTED",
        "FAILED_PRECONDITION",
        "ABORTED",
        "OUT_OF_RANGE",
        "UNIMPLEMENTED",
        "INTERNAL",
        "UNAVAILABLE",
        "DATA_LOSS",
        "UNAUTHENTICATED",
    }
)
SPAN_STATUS_CODES = frozenset({"UNSET", "OK", "ERROR"})
_WORKFLOW_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ATTRIBUTE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,254}$")
TEnum = TypeVar("TEnum", bound=Enum)


def _required_text(data: Mapping[str, Any], key: str, kind: str) -> str:
    if not isinstance(data, Mapping):
        raise ComponentDataError(f"{kind} must be an object")
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ComponentDataError(f"{kind} requires a non-empty '{key}' string")
    return value.strip()


def _enum_value(enum_type: type[TEnum], value: Any, message: str) -> TEnum:
    try:
        return enum_type(value)
    except ValueError as err:
        raise ComponentDataError(message) from err


def _http_status_codes(value: Any) -> tuple[int, ...]:
    valid = isinstance(value, list) and all(
        isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599
        for code in value
    )
    if not valid:
        raise ComponentDataError("HTTP status codes must be integers between 100 and 599")
    return tuple(sorted(set(value)))


def _named_status_codes(
    value: Any,
    allowed: frozenset[str],
    message: str,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(code, str) and code in allowed for code in value
    ):
        raise ComponentDataError(message)
    return tuple(sorted(set(value)))


def _positive_duration(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ComponentDataError("maxDurationMs must be a positive integer")
    return value


def _required_attributes(value: Any) -> tuple["RequiredAttribute", ...]:
    if not isinstance(value, list):
        raise ComponentDataError("requiredAttributes must be a list")
    attributes = tuple(RequiredAttribute.from_mapping(item) for item in value)
    if len({item.key for item in attributes}) != len(attributes):
        raise ComponentDataError("requiredAttributes cannot contain duplicate keys")
    return tuple(sorted(attributes))


def _criticality(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ComponentDataError("WorkflowScenario requires numeric 'criticality'")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ComponentDataError("WorkflowScenario criticality must be between 0 and 1")
    return result


def _positive_version(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ComponentDataError("WorkflowScenario version must be a positive integer")
    return value


def _utc_timestamp(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, str):
        raise ComponentDataError("WorkflowScenario createdAt must be an ISO 8601 string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ComponentDataError("WorkflowScenario createdAt must be ISO 8601") from err
    if result.tzinfo is None:
        raise ComponentDataError("WorkflowScenario createdAt must include a timezone")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True, order=True)
class RequiredAttribute:
    """Exact-match assertion against a low-cardinality trace attribute."""

    key: str
    value: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RequiredAttribute":
        key = _required_text(data, "key", "RequiredAttribute")
        if not _ATTRIBUTE_KEY.fullmatch(key):
            raise ComponentDataError("RequiredAttribute key has an invalid format")
        value = data.get("value")
        if not isinstance(value, (str, int, float, bool)):
            raise ComponentDataError("RequiredAttribute value must be a JSON scalar")
        return cls(key, str(value))

    def to_mapping(self) -> dict[str, str]:
        return {"key": self.key, "value": self.value}


@dataclass(frozen=True)
class SuccessCondition:
    """Protocol-aware assertions used to decide whether one workflow run succeeded."""

    protocol: SuccessProtocol
    terminal_service: str
    operation_name: str
    http_status_codes: tuple[int, ...] = ()
    grpc_status_codes: tuple[str, ...] = ()
    span_status_codes: tuple[str, ...] = ()
    max_duration_ms: int | None = None
    required_attributes: tuple[RequiredAttribute, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SuccessCondition":
        if not isinstance(data, Mapping):
            raise ComponentDataError("successCondition must be a structured object")
        protocol = _enum_value(
            SuccessProtocol,
            _required_text(data, "protocol", "SuccessCondition"),
            "SuccessCondition protocol must be http, grpc, or span",
        )
        http_codes = _http_status_codes(data.get("httpStatusCodes", []))
        grpc_codes = _named_status_codes(
            data.get("grpcStatusCodes", []),
            GRPC_STATUS_CODES,
            "gRPC status codes must use canonical status names",
        )
        span_codes = _named_status_codes(
            data.get("spanStatusCodes", []),
            SPAN_STATUS_CODES,
            "Span status codes must be UNSET, OK, or ERROR",
        )

        selected = {
            SuccessProtocol.HTTP: http_codes,
            SuccessProtocol.GRPC: grpc_codes,
            SuccessProtocol.SPAN: span_codes,
        }
        if not selected[protocol]:
            raise ComponentDataError(f"SuccessCondition {protocol.value} outcomes cannot be empty")
        if any(values for kind, values in selected.items() if kind is not protocol):
            raise ComponentDataError("SuccessCondition cannot mix protocol-specific outcomes")

        return cls(
            protocol=protocol,
            terminal_service=_required_text(data, "terminalService", "SuccessCondition"),
            operation_name=_required_text(data, "operationName", "SuccessCondition"),
            http_status_codes=http_codes,
            grpc_status_codes=grpc_codes,
            span_status_codes=span_codes,
            max_duration_ms=_positive_duration(data.get("maxDurationMs")),
            required_attributes=_required_attributes(data.get("requiredAttributes", [])),
        )

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "protocol": self.protocol.value,
            "terminalService": self.terminal_service,
            "operationName": self.operation_name,
            "requiredAttributes": [item.to_mapping() for item in self.required_attributes],
        }
        if self.protocol is SuccessProtocol.HTTP:
            result["httpStatusCodes"] = list(self.http_status_codes)
        elif self.protocol is SuccessProtocol.GRPC:
            result["grpcStatusCodes"] = list(self.grpc_status_codes)
        else:
            result["spanStatusCodes"] = list(self.span_status_codes)
        if self.max_duration_ms is not None:
            result["maxDurationMs"] = self.max_duration_ms
        return result


@dataclass(frozen=True)
class WorkflowCondition:
    """Named business branch under which dependency evidence is evaluated."""

    condition_id: str
    description: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "WorkflowCondition":
        condition_id = _required_text(data, "conditionId", "WorkflowCondition")
        if not _WORKFLOW_ID.fullmatch(condition_id):
            raise ComponentDataError(
                "WorkflowCondition conditionId must use lowercase letters, digits, '_' or '-'"
            )
        return cls(
            condition_id=condition_id,
            description=_required_text(data, "description", "WorkflowCondition"),
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "conditionId": self.condition_id,
            "description": self.description,
        }


@dataclass(frozen=True)
class WorkflowScenario:
    """Business input known before trace analysis.

    Reference labels and evidence file locations are intentionally forbidden.
    Versions are immutable; an update creates a new record with ``version + 1``.
    """

    workflow_id: str
    description: str
    criticality: float
    success_condition: SuccessCondition
    entry_service: str
    conditions: tuple[WorkflowCondition, ...] = field(
        default_factory=lambda: (
            WorkflowCondition("default", "All workflow executions"),
        )
    )
    version: int = 1
    lifecycle: ScenarioLifecycle = ScenarioLifecycle.DRAFT
    source: ScenarioSource = ScenarioSource.MANUAL
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = "system"
    change_summary: str = "Initial version"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "WorkflowScenario":
        if not isinstance(data, Mapping):
            raise ComponentDataError("WorkflowScenario must be an object")
        forbidden = {"expectedServices", "expectedEdges", "traceFile"} & data.keys()
        if forbidden:
            names = ", ".join(sorted(forbidden))
            raise ComponentDataError(
                f"WorkflowScenario cannot contain reference/evidence fields: {names}"
            )
        workflow_id = _required_text(data, "workflowId", "WorkflowScenario")
        if not _WORKFLOW_ID.fullmatch(workflow_id):
            raise ComponentDataError(
                "WorkflowScenario workflowId must use lowercase letters, digits, '_' or '-'"
            )
        success = data.get("successCondition")
        if not isinstance(success, Mapping):
            raise ComponentDataError("WorkflowScenario successCondition must be an object")
        raw_conditions = data.get(
            "conditions",
            [{"conditionId": "default", "description": "All workflow executions"}],
        )
        if not isinstance(raw_conditions, list) or not raw_conditions:
            raise ComponentDataError("WorkflowScenario conditions must be a non-empty list")
        conditions = tuple(WorkflowCondition.from_mapping(item) for item in raw_conditions)
        condition_ids = [item.condition_id for item in conditions]
        if len(condition_ids) != len(set(condition_ids)):
            raise ComponentDataError("WorkflowScenario condition IDs must be unique")
        return cls(
            workflow_id=workflow_id,
            description=_required_text(data, "description", "WorkflowScenario"),
            criticality=_criticality(data.get("criticality")),
            success_condition=SuccessCondition.from_mapping(success),
            entry_service=_required_text(data, "entryService", "WorkflowScenario"),
            conditions=conditions,
            version=_positive_version(data.get("version", 1)),
            lifecycle=_enum_value(
                ScenarioLifecycle,
                data.get("lifecycle", "draft"),
                "WorkflowScenario lifecycle is invalid",
            ),
            source=_enum_value(
                ScenarioSource,
                data.get("source", "manual"),
                "WorkflowScenario source is invalid",
            ),
            created_at=_utc_timestamp(data.get("createdAt")),
            created_by=_required_text(data, "createdBy", "WorkflowScenario")
            if "createdBy" in data else "system",
            change_summary=_required_text(data, "changeSummary", "WorkflowScenario")
            if "changeSummary" in data else "Initial version",
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "workflowId": self.workflow_id,
            "description": self.description,
            "criticality": self.criticality,
            "successCondition": self.success_condition.to_mapping(),
            "entryService": self.entry_service,
            "conditions": [item.to_mapping() for item in self.conditions],
            "version": self.version,
            "lifecycle": self.lifecycle.value,
            "source": self.source.value,
            "createdAt": self.created_at.isoformat().replace("+00:00", "Z"),
            "createdBy": self.created_by,
            "changeSummary": self.change_summary,
        }


@dataclass(frozen=True)
class WorkflowEvidenceSource:
    """Descriptor for observed trace evidence; it contains no answer labels."""

    workflow_id: str
    source_kind: str
    trace_file: str
    synthetic: bool

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "WorkflowEvidenceSource":
        workflow_id = _required_text(data, "workflowId", "WorkflowEvidenceSource")
        source_kind = _required_text(data, "sourceKind", "WorkflowEvidenceSource")
        trace_file = _required_text(data, "traceFile", "WorkflowEvidenceSource")
        path = PurePosixPath(trace_file)
        if path.is_absolute() or ".." in path.parts:
            raise ComponentDataError("WorkflowEvidenceSource traceFile must be repository-relative")
        synthetic = data.get("synthetic")
        if not isinstance(synthetic, bool):
            raise ComponentDataError("WorkflowEvidenceSource requires boolean 'synthetic'")
        return cls(workflow_id, source_kind, trace_file, synthetic)


@dataclass(frozen=True, order=True)
class ReferenceEdge:
    source: str
    target: str


def _reference_services(data: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    service_sets = {
        "expectedServices": data["expectedServices"],
        "mandatoryServices": data["mandatoryServices"],
        "optionalServices": data["optionalServices"],
    }
    for field_name, values in service_sets.items():
        if not isinstance(values, list) or not all(
            isinstance(item, str) and item.strip() for item in values
        ):
            raise ComponentDataError(
                f"ReferenceWorkflow {field_name} must be strings"
            )
    mandatory = set(service_sets["mandatoryServices"])
    optional = set(service_sets["optionalServices"])
    if mandatory & optional:
        raise ComponentDataError(
            "ReferenceWorkflow mandatoryServices and optionalServices must not overlap"
        )
    if set(service_sets["expectedServices"]) != mandatory | optional:
        raise ComponentDataError(
            "ReferenceWorkflow expectedServices must equal the union of "
            "mandatoryServices and optionalServices"
        )
    return mandatory, optional


def _reference_edges(values: Any, field_name: str) -> set[ReferenceEdge]:
    if not isinstance(values, list):
        raise ComponentDataError(f"ReferenceWorkflow {field_name} must be a list")
    if any(not isinstance(item, Mapping) for item in values):
        raise ComponentDataError("Each reference edge must be an object")
    return {
        ReferenceEdge(
            source=_required_text(item, "source", "ReferenceEdge"),
            target=_required_text(item, "target", "ReferenceEdge"),
        )
        for item in values
    }


def _validate_reference_edges(
    expected: set[ReferenceEdge],
    mandatory: set[ReferenceEdge],
    optional: set[ReferenceEdge],
    evaluated_non_edges: set[ReferenceEdge],
    mandatory_services: set[str],
    optional_services: set[str],
) -> None:
    if mandatory & optional:
        raise ComponentDataError(
            "ReferenceWorkflow mandatoryEdges and optionalEdges must not overlap"
        )
    if expected != mandatory | optional:
        raise ComponentDataError(
            "ReferenceWorkflow expectedEdges must equal the union of "
            "mandatoryEdges and optionalEdges"
        )
    expected_services = mandatory_services | optional_services
    if expected & evaluated_non_edges:
        raise ComponentDataError(
            "ReferenceWorkflow expectedEdges and evaluatedNonEdges must not overlap"
        )
    if any(
        edge.source not in expected_services or edge.target not in expected_services
        for edge in expected | evaluated_non_edges
    ):
        raise ComponentDataError(
            "ReferenceWorkflow edge endpoints must be declared in expectedServices"
        )
    if any(edge.source == edge.target for edge in evaluated_non_edges):
        raise ComponentDataError(
            "ReferenceWorkflow evaluatedNonEdges must not contain self edges"
        )
    if any(
        edge.source not in mandatory_services or edge.target not in mandatory_services
        for edge in mandatory
    ):
        raise ComponentDataError(
            "ReferenceWorkflow mandatoryEdges must connect mandatory services"
        )


@dataclass(frozen=True)
class ReferenceWorkflow:
    """Evaluation-only service labels and expected edge set."""

    workflow_id: str
    mandatory_services: tuple[str, ...]
    optional_services: tuple[str, ...]
    mandatory_edges: tuple[ReferenceEdge, ...]
    optional_edges: tuple[ReferenceEdge, ...]
    evaluated_non_edges: tuple[ReferenceEdge, ...]
    synthetic: bool

    @property
    def expected_services(self) -> tuple[str, ...]:
        """Return the combined service-discovery ground truth."""
        return tuple(sorted(set(self.mandatory_services + self.optional_services)))

    @property
    def expected_edges(self) -> tuple[ReferenceEdge, ...]:
        """Return the combined edge-discovery ground truth."""
        return tuple(sorted(set(self.mandatory_edges + self.optional_edges)))

    @property
    def evaluated_edge_universe(self) -> tuple[ReferenceEdge, ...]:
        """Return only independently labelled positive and negative edges."""
        return tuple(sorted(set(self.expected_edges + self.evaluated_non_edges)))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ReferenceWorkflow":
        workflow_id = _required_text(data, "workflowId", "ReferenceWorkflow")
        required_fields = {
            "expectedServices",
            "mandatoryServices",
            "optionalServices",
            "expectedEdges",
            "mandatoryEdges",
            "optionalEdges",
        }
        missing_fields = sorted(required_fields - data.keys())
        if missing_fields:
            raise ComponentDataError(
                "ReferenceWorkflow requires expectedServices, mandatoryServices, "
                "optionalServices, expectedEdges, mandatoryEdges, and optionalEdges"
            )
        mandatory_services, optional_services = _reference_services(data)
        edges = _reference_edges(data["expectedEdges"], "expectedEdges")
        mandatory_edges = _reference_edges(data["mandatoryEdges"], "mandatoryEdges")
        optional_edges = _reference_edges(data["optionalEdges"], "optionalEdges")
        evaluated_non_edges = _reference_edges(
            data.get("evaluatedNonEdges", []), "evaluatedNonEdges"
        )
        _validate_reference_edges(
            edges,
            mandatory_edges,
            optional_edges,
            evaluated_non_edges,
            mandatory_services,
            optional_services,
        )
        synthetic = data.get("synthetic")
        if not isinstance(synthetic, bool):
            raise ComponentDataError("ReferenceWorkflow requires boolean 'synthetic'")
        return cls(
            workflow_id=workflow_id,
            mandatory_services=tuple(sorted(mandatory_services)),
            optional_services=tuple(sorted(optional_services)),
            mandatory_edges=tuple(sorted(mandatory_edges)),
            optional_edges=tuple(sorted(optional_edges)),
            evaluated_non_edges=tuple(sorted(evaluated_non_edges)),
            synthetic=synthetic,
        )
