"""Safe, generic HTTP executor for reviewed workflow probe definitions."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

from app.engine.workflow_dependency_modeling.workflow_probe_models import (
    ExtractionSource,
    HttpProbeStep,
    WorkflowProbeDefinition,
    WorkflowRunResult,
    WorkflowRunStepResult,
)


_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_VARIABLE = re.compile(r"{{\s*([A-Za-z][A-Za-z0-9_.-]{0,63})\s*}}")


def approved_application_url(
    value: Any,
    *,
    allowed_hosts: set[str] | None = None,
) -> str:
    """Validate an operator-supplied target against the deployment allowlist."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Application base URL is required")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Application base URL must be absolute HTTP or HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "Application base URL cannot contain credentials, query, or fragment"
        )
    allowed = allowed_hosts or {
        item.strip().lower()
        for item in os.getenv(
            "WORKFLOW_TARGET_ALLOWED_HOSTS",
            "localhost,127.0.0.1,::1",
        ).split(",")
        if item.strip()
    }
    if parsed.hostname.lower() not in allowed:
        raise ValueError(
            "Application host is not approved; configure "
            "WORKFLOW_TARGET_ALLOWED_HOSTS"
        )
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("Workflow run timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _traceparent(trace_id: str) -> str:
    if not _TRACE_ID.fullmatch(trace_id):
        raise ValueError(
            "traceId must contain exactly 32 lowercase hexadecimal characters"
        )
    return f"00-{trace_id}-{uuid4().hex[:16]}-01"


def _render_text(value: str, variables: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise ValueError(f"Workflow variable '{name}' is not available")
        return variables[name]

    return _VARIABLE.sub(replace, value)


def _render_json(value: Any, variables: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return _render_text(value, variables)
    if isinstance(value, list):
        return [_render_json(item, variables) for item in value]
    if isinstance(value, dict):
        return {
            key: _render_json(item, variables)
            for key, item in value.items()
        }
    return value


def _extract_json_field(payload: Any, selector: str) -> Any:
    current = payload
    for part in selector.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ValueError(f"JSON response does not contain '{selector}'")
        current = current[part]
    if not isinstance(current, (str, int, float, bool)):
        raise ValueError(f"Extracted value '{selector}' must be a JSON scalar")
    return current


def _extract_variables(
    step: HttpProbeStep,
    response: httpx.Response,
) -> dict[str, str]:
    result: dict[str, str] = {}
    parsed_json: Any = None
    json_loaded = False
    for name, extraction in step.extract.items():
        if extraction.source is ExtractionSource.RESPONSE_HEADER:
            value = response.headers.get(extraction.selector)
            if value is None:
                raise ValueError(
                    f"Response does not contain header '{extraction.selector}'"
                )
        else:
            if not json_loaded:
                try:
                    parsed_json = response.json()
                except ValueError as err:
                    raise ValueError("Response is not valid JSON") from err
                json_loaded = True
            value = _extract_json_field(parsed_json, extraction.selector)
        result[name] = str(value)
    return result


def _step_failure_reason(
    step: HttpProbeStep,
    response: httpx.Response,
) -> str | None:
    if response.status_code not in step.expected_status_codes:
        expected = ", ".join(str(code) for code in step.expected_status_codes)
        return f"returned HTTP {response.status_code}; expected one of {expected}"
    missing = [marker for marker in step.response_contains if marker not in response.text]
    if missing:
        return "response did not contain required marker(s): " + ", ".join(missing)
    return None


def _secret_headers(
    definition: WorkflowProbeDefinition,
    secrets: Mapping[str, str],
) -> dict[str, dict[str, str]]:
    resolved: dict[str, dict[str, str]] = {}
    for step in definition.steps:
        headers: dict[str, str] = {}
        for header, reference in step.secret_headers.items():
            value = secrets.get(reference)
            if not value:
                raise ValueError(
                    f"Required secret environment variable '{reference}' is not set"
                )
            headers[header] = value
        resolved[step.name] = headers
    return resolved


def _execute_probe_step(
    client: httpx.Client,
    step: HttpProbeStep,
    variables: dict[str, str],
    secret_headers: Mapping[str, str],
    trace_id: str | None,
) -> WorkflowRunStepResult:
    headers = {
        key: _render_text(value, variables) for key, value in step.headers.items()
    }
    headers.update(secret_headers)
    if trace_id is not None:
        headers["traceparent"] = _traceparent(trace_id)
    json_body = (
        _render_json(step.json_body, variables)
        if step.json_body is not None
        else None
    )
    form_body = {
        key: _render_text(value, variables) for key, value in step.form_body.items()
    }
    rendered_path = _render_text(step.path, variables)
    rendered_url = urlsplit(rendered_path)
    if (
        rendered_url.scheme or rendered_url.netloc
        or not rendered_path.startswith("/") or rendered_path.startswith("//")
    ):
        raise ValueError("Rendered request path must remain relative to the application URL")
    response = client.request(
        method=step.method,
        url=rendered_path,
        params={
            key: _render_text(value, variables) for key, value in step.query.items()
        },
        headers=headers,
        json=json_body,
        data=form_body or None,
    )
    failure = _step_failure_reason(step, response)
    if failure is None:
        try:
            variables.update(_extract_variables(step, response))
        except ValueError as err:
            failure = str(err)
    succeeded = failure is None
    return WorkflowRunStepResult(
        name=step.name,
        method=step.method,
        path=step.path,
        status_code=response.status_code,
        succeeded=succeeded,
        reason="Step satisfied its reviewed assertions" if succeeded else failure,
    )


def run_http_workflow_probe(
    base_url: str,
    definition: WorkflowProbeDefinition,
    *,
    trace_id: str | None = None,
    timeout_seconds: float = 15.0,
    transport: httpx.BaseTransport | None = None,
    started_at: datetime | None = None,
    secrets: Mapping[str, str] | None = None,
    allowed_hosts: set[str] | None = None,
) -> WorkflowRunResult:
    """Trace each request and report the terminal or first failing step's trace."""
    approved_url = approved_application_url(base_url, allowed_hosts=allowed_hosts)
    if not 1 <= timeout_seconds <= 60:
        raise ValueError("timeoutSeconds must be between 1 and 60")
    resolved_trace_id = trace_id or uuid4().hex
    _traceparent(resolved_trace_id)
    start = _utc(started_at)
    variables = dict(definition.initial_variables)
    resolved_secrets = _secret_headers(definition, secrets or os.environ)
    results: list[WorkflowRunStepResult] = []
    step_trace_ids: set[str] = {resolved_trace_id}

    with httpx.Client(
        base_url=approved_url,
        timeout=timeout_seconds,
        follow_redirects=False,
        transport=transport,
    ) as client:
        for step in definition.steps:
            # Each HTTP request is a separate trace root. Reusing one trace ID
            # would create disconnected roots and invalidate trace quality.
            step_trace_id = resolved_trace_id if step.name == definition.measured_step else uuid4().hex
            while step_trace_id in step_trace_ids and step.name != definition.measured_step:
                step_trace_id = uuid4().hex
            step_trace_ids.add(step_trace_id)
            result = _execute_probe_step(
                client,
                step,
                variables,
                resolved_secrets[step.name],
                step_trace_id,
            )
            results.append(result)
            if not result.succeeded:
                return WorkflowRunResult(
                    trace_id=step_trace_id,
                    started_at=start,
                    finished_at=datetime.now(timezone.utc),
                    business_success=False,
                    reason=f"Workflow stopped at step '{step.name}': {result.reason}",
                    steps=tuple(results),
                )

    return WorkflowRunResult(
        trace_id=resolved_trace_id,
        started_at=start,
        finished_at=datetime.now(timezone.utc),
        business_success=True,
        reason="Every workflow step satisfied its reviewed assertions",
        steps=tuple(results),
    )
