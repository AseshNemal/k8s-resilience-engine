"""Convert operator-reviewed workflow setup into executable probe definitions."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping
from urllib.parse import quote

from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.engine.workflow_dependency_modeling.workflow_probe_models import (
    WorkflowProbeDefinition,
)


_HTTP_OPERATION = re.compile(
    r"^(GET|POST|PUT|PATCH|DELETE)\s+(/\S*)$",
    re.IGNORECASE,
)
_ROUTE_VARIABLE = re.compile(r"(?<!{){([A-Za-z][A-Za-z0-9_.-]{0,63})}(?!})")
_VALUE_VARIABLE = re.compile(r"{{\s*([A-Za-z][A-Za-z0-9_.-]{0,63})\s*}}")
_SETUP_FIELDS = {
    "mode",
    "expectedStatusCodes",
    "initialVariables",
    "request",
    "executionDefinition",
    "successStatuses",
    "actionInputs",
    "preparationSteps",
}
_REQUEST_FIELDS = {
    "pathVariables",
    "query",
    "headers",
    "secretHeaders",
    "jsonBody",
    "formBody",
    "responseContains",
}


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ComponentDataError(f"{field} must be an object")
    return value


def _reject_unknown(data: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ComponentDataError(
            f"{field} contains unsupported field(s): {', '.join(unknown)}"
        )


def workflow_setup_requirements(operation_name: str) -> dict[str, Any]:
    """Describe only request structure actually present in the selected operation."""
    match = _HTTP_OPERATION.fullmatch(operation_name.strip())
    return {
        "guidedSupported": match is not None,
        "action": (
            None if match is None else {
                "method": match.group(1).upper(),
                "path": match.group(2),
                "pathParameters": sorted(set(_ROUTE_VARIABLE.findall(match.group(2)))),
            }
        ),
        "maximumPreparationSteps": 19,
        "bodyFormats": ["none", "form", "json"],
        "jsonValueTypes": ["text", "number", "boolean", "null", "json"],
        "captureSources": ["json_field", "response_header"],
        "limitations": [
            "Traces identify recorded calls, but do not supply request bodies, authentication or prerequisite business steps.",
            "The runner shares cookies between steps and measures the selected action after preparation succeeds.",
        ],
    }


def _guided_statuses(value: Any, subject: str) -> list[int]:
    if not isinstance(value, str):
        raise ComponentDataError(f"{subject}: enter comma-separated HTTP success codes")
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(not re.fullmatch(r"[1-5]\d{2}", part) for part in parts):
        raise ComponentDataError(f"{subject}: enter HTTP success codes between 100 and 599")
    return sorted({int(part) for part in parts})


def _input_rows(value: Any, subject: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or len(value) > 100:
        raise ComponentDataError(f"{subject} must contain at most 100 input rows")
    rows = []
    names: set[str] = set()
    for raw in value:
        row = _mapping(raw, subject)
        _reject_unknown(row, {"name", "value", "type", "source", "selector"}, subject)
        name, item = row.get("name", ""), row.get("value", "")
        if not isinstance(name, str) or not isinstance(item, str):
            raise ComponentDataError(f"{subject}: field names and values must be text")
        if not name.strip() and not item and not row.get("selector"):
            continue
        if not name.strip():
            raise ComponentDataError(f"{subject}: enter a name for every value")
        identity = name.strip().lower() if subject.endswith("headers") else name.strip()
        if identity in names:
            raise ComponentDataError(f"{subject}: duplicate field '{name.strip()}'")
        names.add(identity)
        rows.append({**row, "name": name.strip(), "value": item})
    return rows


def _body_value(row: Mapping[str, Any]) -> Any:
    kind, value = row.get("type", "text"), row["value"]
    if kind == "text":
        return value
    if kind == "null":
        return None
    if kind not in {"number", "boolean", "json"}:
        raise ComponentDataError(f"Unknown value type for body field '{row['name']}'")
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError) as err:
        raise ComponentDataError(f"Body field '{row['name']}' must contain a valid {kind} value") from err
    if kind == "number" and (isinstance(parsed, bool) or not isinstance(parsed, (int, float))):
        raise ComponentDataError(f"Body field '{row['name']}' must be a number")
    if kind == "boolean" and not isinstance(parsed, bool):
        raise ComponentDataError(f"Body field '{row['name']}' must be true or false")
    try:
        json.dumps(parsed, allow_nan=False)
    except ValueError as err:
        raise ComponentDataError(f"Body field '{row['name']}' contains a non-finite number") from err
    return parsed


def _guided_inputs(value: Any, subject: str) -> tuple[dict[str, Any], dict[str, str]]:
    inputs = _mapping(value, subject)
    _reject_unknown(inputs, {"pathParameters", "queryParameters", "headers", "bodyFormat", "bodyFields", "responseText"}, subject)
    parameters = {
        row["name"]: row["value"]
        for row in _input_rows(inputs.get("pathParameters", []), subject + ".pathParameters")
    }
    if any(not value.strip() for value in parameters.values()):
        raise ComponentDataError(f"{subject}: path parameters require non-empty values")
    request: dict[str, Any] = {
        "query": {row["name"]: row["value"] for row in _input_rows(inputs.get("queryParameters", []), subject + ".queryParameters")},
        "headers": {}, "secretHeaders": {},
    }
    for row in _input_rows(inputs.get("headers", []), subject + ".headers"):
        source = row.get("source", "literal")
        if source not in {"literal", "environment"}:
            raise ComponentDataError(f"{subject}: invalid header source")
        request["secretHeaders" if source == "environment" else "headers"][row["name"]] = row["value"]
    body = _input_rows(inputs.get("bodyFields", []), subject + ".bodyFields")
    body_format = inputs.get("bodyFormat", "none")
    if body_format == "form":
        request["formBody"] = {row["name"]: row["value"] for row in body}
    elif body_format == "json":
        request["jsonBody"] = {row["name"]: _body_value(row) for row in body}
    elif body_format != "none" or body:
        raise ComponentDataError(f"{subject}: select form or JSON when providing a request body")
    marker = inputs.get("responseText", "")
    if not isinstance(marker, str):
        raise ComponentDataError(f"{subject}: required response text must be text")
    if marker:
        request["responseContains"] = [marker]
    return request, parameters


def _guided_sequence(entry_service: str, operation_name: str, setup: Mapping[str, Any]) -> WorkflowProbeDefinition:
    _reject_unknown(setup, {"mode", "successStatuses", "actionInputs", "preparationSteps"}, "workflowSetup")
    preparation = setup.get("preparationSteps", [])
    if not isinstance(preparation, list) or len(preparation) > 19:
        raise ComponentDataError("A workflow can contain at most 19 preparation steps")
    action_codes = _guided_statuses(setup.get("successStatuses"), "Selected action")
    available: set[str] = set()
    steps: list[dict[str, Any]] = []
    raw_steps = [
        *preparation,
        {
            "operation": operation_name,
            "successStatuses": setup["successStatuses"],
            "inputs": setup.get("actionInputs", {}),
        },
    ]
    for index, raw in enumerate(raw_steps):
        item = _mapping(raw, f"Step {index + 1}")
        _reject_unknown(item, {"operation", "successStatuses", "inputs", "captures"}, f"Step {index + 1}")
        operation = item.get("operation")
        match = _HTTP_OPERATION.fullmatch(operation.strip()) if isinstance(operation, str) else None
        if match is None:
            raise ComponentDataError(f"Step {index + 1}: choose an HTTP method and application-relative path")
        request, parameters = _guided_inputs(item.get("inputs", {}), f"Step {index + 1}")
        path = match.group(2)
        required = set(_ROUTE_VARIABLE.findall(path))
        if required != set(parameters):
            missing, unexpected = sorted(required - set(parameters)), sorted(set(parameters) - required)
            raise ComponentDataError(f"Step {index + 1}: path parameters need values for {missing}; unexpected names {unexpected}")
        # Literal path inputs are encoded per request; captured values stay runner variables.
        def path_value(token: re.Match[str]) -> str:
            value = parameters[token.group(1)]
            return value if _VALUE_VARIABLE.fullmatch(value) else quote(value, safe="")

        path = _ROUTE_VARIABLE.sub(path_value, path)
        captures = {}
        for row in _input_rows(item.get("captures", []), f"Step {index + 1}.captures"):
            captures[row["name"]] = {"source": row.get("source"), "selector": row.get("selector")}
        step = {
            "name": "workflow_action" if index == len(preparation) else f"prepare_{index + 1}",
            "method": match.group(1).upper(),
            "path": path,
            "expectedStatusCodes": _guided_statuses(item.get("successStatuses"), f"Step {index + 1}"),
            **request,
            "extract": captures,
        }
        used = set(_VALUE_VARIABLE.findall(json.dumps({
            key: value for key, value in step.items() if key != "extract"
        })))
        if used - available:
            raise ComponentDataError(f"Step {index + 1}: values are not available from earlier responses: {', '.join(sorted(used - available))}")
        steps.append(step)
        available.update(captures)
    return WorkflowProbeDefinition.from_mapping({
        "runnerType": "http_sequence",
        "steps": steps,
        "measuredStep": "workflow_action",
        "successCondition": {
            "protocol": "http",
            "terminalService": entry_service,
            "operationName": operation_name,
            "httpStatusCodes": action_codes,
            "requiredAttributes": [],
        },
    })


def _observed_entry_probe(
    entry_service: str,
    operation_name: str,
    setup: Mapping[str, Any],
) -> WorkflowProbeDefinition:
    match = _HTTP_OPERATION.fullmatch(operation_name.strip())
    if match is None:
        raise ComponentDataError(
            "The observed operation is not an executable HTTP method/path; "
            "import a reviewed execution definition"
        )
    method, observed_path = match.groups()
    request = _mapping(setup.get("request", {}), "workflowSetup.request")
    _reject_unknown(request, _REQUEST_FIELDS, "workflowSetup.request")

    initial_variables = dict(
        _mapping(setup.get("initialVariables", {}), "initialVariables")
    )
    path_variables = _mapping(request.get("pathVariables", {}), "pathVariables")
    initial_variables.update(path_variables)
    required_variables = set(_ROUTE_VARIABLE.findall(observed_path))
    missing = sorted(required_variables - set(path_variables))
    if missing:
        raise ComponentDataError(
            "Observed route requires pathVariables for: " + ", ".join(missing)
        )
    path = _ROUTE_VARIABLE.sub(lambda item: "{{" + item.group(1) + "}}", observed_path)

    step: dict[str, Any] = {
        "name": "invoke_observed_entry",
        "method": method.upper(),
        "path": path,
        "expectedStatusCodes": setup.get("expectedStatusCodes"),
    }
    for field in (
        "query",
        "headers",
        "secretHeaders",
        "jsonBody",
        "formBody",
        "responseContains",
    ):
        if field in request:
            step[field] = request[field]

    return WorkflowProbeDefinition.from_mapping(
        {
            "runnerType": "http_sequence",
            "initialVariables": initial_variables,
            "steps": [step],
            "successCondition": {
                "protocol": "http",
                "terminalService": entry_service,
                "operationName": operation_name,
                "httpStatusCodes": setup.get("expectedStatusCodes"),
                "requiredAttributes": [],
            },
        }
    )


def build_workflow_probe(
    entry_service: str,
    operation_name: str,
    setup_data: Mapping[str, Any],
) -> WorkflowProbeDefinition:
    """Build a reviewed probe without inventing unobserved business semantics."""
    setup = _mapping(setup_data, "workflowSetup")
    _reject_unknown(setup, _SETUP_FIELDS, "workflowSetup")
    mode = setup.get("mode")
    if mode == "guided_sequence":
        return _guided_sequence(entry_service, operation_name, setup)
    if mode == "observed_entry":
        if "executionDefinition" in setup:
            raise ComponentDataError(
                "observed_entry setup cannot include executionDefinition"
            )
        _reject_unknown(setup, {"mode", "expectedStatusCodes", "initialVariables", "request"}, "workflowSetup")
        return _observed_entry_probe(entry_service, operation_name, setup)
    if mode == "imported_definition":
        forbidden = {
            "expectedStatusCodes",
            "initialVariables",
            "request",
        } & setup.keys()
        if forbidden:
            raise ComponentDataError(
                "imported_definition cannot include observed-entry fields"
            )
        _reject_unknown(setup, {"mode", "executionDefinition"}, "workflowSetup")
        definition = _mapping(
            setup.get("executionDefinition"),
            "workflowSetup.executionDefinition",
        )
        return WorkflowProbeDefinition.from_mapping(definition)
    raise ComponentDataError(
        "workflowSetup mode must be observed_entry, guided_sequence or imported_definition"
    )
