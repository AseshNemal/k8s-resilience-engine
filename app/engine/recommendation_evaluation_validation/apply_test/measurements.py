"""Functional workflow checks over observed topology and explicitly configured HTTP probes."""
import json
import os
from dataclasses import asdict
from pathlib import Path

from app.engine.recommendation_evaluation_validation.declared_workflow import declared_workflow_path
from app.engine.recommendation_evaluation_validation.validation.workflow_probe import ProbeSpec, WorkflowProbe
from app.integrations.probe.http_fetch import LoopbackHttpFetch, require_loopback


def configured_checks():
    path = os.environ.get("RESILIENCE_WORKFLOW_PROBES_PATH") or declared_workflow_path()
    if not path:
        return {}, "No workflow HTTP checks are configured."
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(256 * 1024 + 1)
        if len(data) > 256 * 1024:
            raise ValueError("file too large")
        checks = json.loads(data).get("workflowChecks", {})
        if not isinstance(checks, dict):
            raise ValueError("workflowChecks must be an object")
        return checks, None
    except (OSError, ValueError, TypeError, AttributeError):
        return {}, "The configured workflow checks could not be read."


def measure_workflows(live, *, fetch=None, on_check=lambda _text: None):
    definitions = live.details.get("portfolioWorkflows") or []
    if not definitions:
        return {"rows": [], "complete": False, "error": "Workflow definitions were not supplied by the evaluation."}
    checks, config_error = configured_checks()
    services = {s["name"]: s for s in live.details["currentDeployment"]["services"]}
    results = []
    for definition in definitions:
        workflow_id = definition.get("workflow_id") or definition.get("workflowId")
        mandatory = definition.get("mandatory_services") or definition.get("mandatoryServices") or []
        missing = [name for name in mandatory if name not in services or not any(
            p.get("ready") is True for p in services[name].get("pods", []))]
        row = {"workflowId": workflow_id, "mandatoryServices": mandatory, "unreadyServices": missing,
               "readiness": "READY" if not missing else "NOT_READY", "httpStatus": "NOT_CONFIGURED",
               "samples": [], "reason": config_error or "No HTTP check is configured for this workflow."}
        check = checks.get(workflow_id)
        if check is not None:
            try:
                url = require_loopback(check["url"])
                from urllib.parse import urlsplit
                if urlsplit(url).username or urlsplit(url).password:
                    raise ValueError("Probe URLs cannot contain credentials")
                required, forbidden = check.get("mustContain", []), check.get("mustNotContain", [])
                if not all(isinstance(items, list) and all(isinstance(t, str) and t for t in items) for items in (required, forbidden)):
                    raise ValueError("Probe assertions must be lists of non-empty strings")
                if not required and not forbidden:
                    raise ValueError("A workflow check needs response-content assertions; HTTP 200 alone is insufficient")
                spec = ProbeSpec(workflow_id, url, expected_status=int(check.get("expectedStatus", 200)),
                                 required_text=tuple(required), forbidden_text=tuple(forbidden), timeout_s=3, min_samples=3)
                probe = WorkflowProbe(spec, fetch or LoopbackHttpFetch())
                for index in range(3):
                    on_check(f"GET {url} — {workflow_id}, sample {index + 1}/3")
                    row["samples"].append(asdict(probe.sample()))
                row.update(httpStatus="PASSED" if all(s["ok"] for s in row["samples"]) else "FAILED", reason=None)
            except (ValueError, KeyError, TypeError):
                row.update(httpStatus="INVALID_CHECK", reason="The configured HTTP check is invalid or lacks response assertions.")
        results.append(row)
    return {"rows": results, "complete": bool(results) and all(r["httpStatus"] in ("PASSED", "FAILED") for r in results),
            "meaning": "Readiness checks mandatory service pods. HTTP checks measure only the configured entry point and response assertions, without injected failures."}
