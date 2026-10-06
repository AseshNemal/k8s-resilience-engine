"""Convert operator-reviewed workflow setup into executable probe definitions."""

from __future__ import annotations

import re
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.engine.workflow_dependency_modeling.workflow_probe_models import (
    WorkflowProbeDefinition,
)


_HTTP_OPERATION = re.compile(
    r"^(GET|POST|PUT|PATCH|DELETE)\s+(/\S*)$",
    re.IGNORECASE,
)
_ROUTE_VARIABLE = re.compile(r"{([A-Za-z][A-Za-z0-9_.-]{0,63})}")
_SETUP_FIELDS = {
    "mode",
    "expectedStatusCodes",
    "initialVariables",
    "request",
    "executionDefinition",
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
    if mode == "observed_entry":
        if "executionDefinition" in setup:
            raise ComponentDataError(
                "observed_entry setup cannot include executionDefinition"
            )
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
        definition = _mapping(
            setup.get("executionDefinition"),
            "workflowSetup.executionDefinition",
        )
        return WorkflowProbeDefinition.from_mapping(definition)
    raise ComponentDataError(
        "workflowSetup mode must be observed_entry or imported_definition"
    )
