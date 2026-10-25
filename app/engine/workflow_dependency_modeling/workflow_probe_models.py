"""Generic, serializable workflow-execution contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import re
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.models import (
    ComponentDataError,
    SuccessCondition,
)
from .execution_models import AssertionResult


_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_SENSITIVE_HEADERS = frozenset(
    {"authorization", "cookie", "proxy-authorization", "x-api-key"}
)
_PROBE_STEP = "Probe step"


class WorkflowRunnerType(str, Enum):
    HTTP_SEQUENCE = "http_sequence"


class ExtractionSource(str, Enum):
    JSON_FIELD = "json_field"
    RESPONSE_HEADER = "response_header"


def _required_text(data: Mapping[str, Any], key: str, subject: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ComponentDataError(f"{subject} requires non-empty '{key}'")
    return value.strip()


def _status_codes(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ComponentDataError("Probe step requires expectedStatusCodes")
    if any(
        isinstance(code, bool) or not isinstance(code, int) or not 100 <= code <= 599
        for code in value
    ):
        raise ComponentDataError("expectedStatusCodes must contain HTTP status codes")
    return tuple(sorted(set(value)))


def _string_mapping(value: Any, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ComponentDataError(f"{field} must be an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ComponentDataError(f"{field} keys must be non-empty text")
        if not isinstance(item, (str, int, float, bool)):
            raise ComponentDataError(f"{field} values must be JSON scalars")
        result[key.strip()] = str(item)
    return result


def _json_object(value: Any, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ComponentDataError(f"{field} must be an object")
    try:
        serialized = json.dumps(value)
    except (TypeError, ValueError) as err:
        raise ComponentDataError(f"{field} must contain JSON-compatible values") from err
    if len(serialized.encode("utf-8")) > 65_536:
        raise ComponentDataError(f"{field} exceeds the 64 KiB limit")
    return json.loads(serialized)


def _probe_identity(data: Mapping[str, Any]) -> tuple[str, str, str]:
    name = _required_text(data, "name", _PROBE_STEP)
    if not _NAME.fullmatch(name):
        raise ComponentDataError("Probe step name has an invalid format")
    method = _required_text(data, "method", _PROBE_STEP).upper()
    if method not in _HTTP_METHODS:
        raise ComponentDataError("Probe step HTTP method is not supported")
    path = _required_text(data, "path", _PROBE_STEP)
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        raise ComponentDataError("Probe step path must be relative to the application URL")
    if "#" in path:
        raise ComponentDataError("Probe step path cannot contain a fragment")
    return name, method, path


def _probe_headers(data: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    headers = _string_mapping(data.get("headers"), "headers")
    if _SENSITIVE_HEADERS & {key.lower() for key in headers}:
        raise ComponentDataError(
            "Sensitive headers must use secretHeaders environment references"
        )
    secret_headers = _string_mapping(data.get("secretHeaders"), "secretHeaders")
    if any(not _ENVIRONMENT_NAME.fullmatch(value) for value in secret_headers.values()):
        raise ComponentDataError(
            "secretHeaders values must be environment-variable names"
        )
    if {key.lower() for key in headers} & {key.lower() for key in secret_headers}:
        raise ComponentDataError("A header cannot have both literal and secret values")
    return headers, secret_headers


def _response_markers(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ComponentDataError("responseContains must be a list of non-empty text")
    return tuple(value)


def _response_extractions(value: Any) -> dict[str, "ResponseExtraction"]:
    if not isinstance(value, Mapping):
        raise ComponentDataError("extract must be an object")
    result: dict[str, ResponseExtraction] = {}
    for variable, definition in value.items():
        if not isinstance(variable, str) or not _NAME.fullmatch(variable):
            raise ComponentDataError("Extracted variable name has an invalid format")
        if not isinstance(definition, Mapping):
            raise ComponentDataError("Extraction definition must be an object")
        result[variable] = ResponseExtraction.from_mapping(definition)
    return result


@dataclass(frozen=True)
class ResponseExtraction:
    source: ExtractionSource
    selector: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ResponseExtraction":
        try:
            source = ExtractionSource(data.get("source"))
        except ValueError as err:
            raise ComponentDataError(
                "Extraction source must be json_field or response_header"
            ) from err
        selector = _required_text(data, "selector", "Response extraction")
        if source is ExtractionSource.JSON_FIELD:
            parts = selector.split(".")
            if any(not _NAME.fullmatch(part) for part in parts):
                raise ComponentDataError("JSON extraction selector is invalid")
        elif "\r" in selector or "\n" in selector:
            raise ComponentDataError("Response header selector is invalid")
        return cls(source=source, selector=selector)

    def to_mapping(self) -> dict[str, str]:
        return {"source": self.source.value, "selector": self.selector}


@dataclass(frozen=True)
class HttpProbeStep:
    name: str
    method: str
    path: str
    expected_status_codes: tuple[int, ...]
    query: dict[str, str]
    headers: dict[str, str]
    secret_headers: dict[str, str]
    json_body: dict[str, Any] | None
    form_body: dict[str, str]
    response_contains: tuple[str, ...]
    extract: dict[str, ResponseExtraction]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "HttpProbeStep":
        name, method, path = _probe_identity(data)
        headers, secret_headers = _probe_headers(data)
        json_body = _json_object(data.get("jsonBody"), "jsonBody")
        form_body = _string_mapping(data.get("formBody"), "formBody")
        if json_body is not None and form_body:
            raise ComponentDataError("Probe step cannot combine jsonBody and formBody")

        return cls(
            name=name,
            method=method,
            path=path,
            expected_status_codes=_status_codes(data.get("expectedStatusCodes")),
            query=_string_mapping(data.get("query"), "query"),
            headers=headers,
            secret_headers=secret_headers,
            json_body=json_body,
            form_body=form_body,
            response_contains=_response_markers(data.get("responseContains", [])),
            extract=_response_extractions(data.get("extract", {})),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "method": self.method,
            "path": self.path,
            "expectedStatusCodes": list(self.expected_status_codes),
            "query": dict(self.query),
            "headers": dict(self.headers),
            "secretHeaders": dict(self.secret_headers),
            "jsonBody": self.json_body,
            "formBody": dict(self.form_body),
            "responseContains": list(self.response_contains),
            "extract": {
                name: definition.to_mapping()
                for name, definition in self.extract.items()
            },
        }


@dataclass(frozen=True)
class WorkflowProbeDefinition:
    runner_type: WorkflowRunnerType
    steps: tuple[HttpProbeStep, ...]
    measured_step: str
    success_condition: SuccessCondition
    initial_variables: dict[str, str]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "WorkflowProbeDefinition":
        if not isinstance(data, Mapping):
            raise ComponentDataError("executionDefinition must be an object")
        try:
            runner_type = WorkflowRunnerType(data.get("runnerType"))
        except ValueError as err:
            raise ComponentDataError("runnerType must be http_sequence") from err
        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 20:
            raise ComponentDataError("HTTP workflow requires between 1 and 20 steps")
        if any(not isinstance(item, Mapping) for item in raw_steps):
            raise ComponentDataError("Each HTTP workflow step must be an object")
        steps = tuple(HttpProbeStep.from_mapping(item) for item in raw_steps)
        if len({item.name for item in steps}) != len(steps):
            raise ComponentDataError("Probe step names must be unique")
        measured_step = str(data.get("measuredStep", steps[-1].name)).strip()
        if measured_step not in {item.name for item in steps}:
            raise ComponentDataError("measuredStep must identify one probe step")
        return cls(
            runner_type=runner_type,
            steps=steps,
            measured_step=measured_step,
            success_condition=SuccessCondition.from_mapping(
                data.get("successCondition", {})
            ),
            initial_variables=_string_mapping(
                data.get("initialVariables"), "initialVariables"
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "runnerType": self.runner_type.value,
            "measuredStep": self.measured_step,
            "steps": [item.to_mapping() for item in self.steps],
            "successCondition": self.success_condition.to_mapping(),
            "initialVariables": dict(self.initial_variables),
        }


@dataclass(frozen=True)
class WorkflowRunStepResult:
    name: str
    method: str
    path: str
    status_code: int | None
    succeeded: bool
    reason: str
    trace_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    assertions: tuple[AssertionResult, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "method": self.method,
            "path": self.path,
            "statusCode": self.status_code,
            "succeeded": self.succeeded,
            "reason": self.reason,
            "traceId": self.trace_id,
            "startedAt": self.started_at.isoformat() if self.started_at else None,
            "finishedAt": self.finished_at.isoformat() if self.finished_at else None,
            "assertions": [item.to_mapping() for item in self.assertions],
        }


@dataclass(frozen=True)
class WorkflowRunResult:
    trace_id: str
    started_at: datetime
    finished_at: datetime
    business_success: bool
    reason: str
    steps: tuple[WorkflowRunStepResult, ...]

    def __post_init__(self) -> None:
        for value in (self.started_at, self.finished_at):
            if value.tzinfo is None:
                raise ValueError("Workflow run timestamps must be timezone-aware")
        if self.finished_at.astimezone(timezone.utc) < self.started_at.astimezone(
            timezone.utc
        ):
            raise ValueError("Workflow run cannot finish before it starts")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "startedAt": self.started_at.astimezone(timezone.utc).isoformat(),
            "finishedAt": self.finished_at.astimezone(timezone.utc).isoformat(),
            "businessSuccess": self.business_success,
            "reason": self.reason,
            "steps": [item.to_mapping() for item in self.steps],
        }
