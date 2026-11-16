"""Copy into the application test project. Standard-library-only evidence exporter.

Each headers() call belongs to ONE HTTP request. Application instrumentation must
honor W3C trace context. Runner status is a report, never dependency validation.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


class Evidence:
    def __init__(self, test_name, namespace, application_url, entry_service, *, success_condition=None):
        target = urlsplit(application_url)
        if target.scheme not in {"http", "https"} or not target.hostname or target.username or target.password or target.query or target.fragment:
            raise ValueError("Use an HTTP(S) application base URL without credentials, query or fragment")
        self.namespace = namespace
        self.execution = {"testName": test_name, "executionId": uuid4().hex, "runner": "pytest",
            "applicationUrl": application_url, "entryService": entry_service,
            "startedAt": datetime.now(timezone.utc).isoformat(), "traceIds": [],
            "assertions": [], "reportedStatus": "unknown", "successCondition": success_condition}

    def headers(self):
        if len(self.execution["traceIds"]) >= 20:
            raise ValueError("Export at most 20 request traces per execution")
        trace_id = uuid4().hex
        self.execution["traceIds"].append(trace_id)
        return {"traceparent": f"00-{trace_id}-{uuid4().hex[:16]}-01"}

    def assertion(self, name, passed):
        if not isinstance(passed, bool):
            raise TypeError("Assertion result must be a boolean")
        self.execution["assertions"].append({"name": name, "passed": passed})

    def outcome_trace(self, trace_id):
        if trace_id not in self.execution["traceIds"]:
            raise ValueError("Outcome trace must belong to this execution")
        self.execution["outcomeTraceId"] = trace_id

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _error, _traceback):
        self.execution["finishedAt"] = datetime.now(timezone.utc).isoformat()
        self.execution["reportedStatus"] = "failed" if exc_type else "passed"
        return False

    def write(self, path):
        self.execution.setdefault("finishedAt", datetime.now(timezone.utc).isoformat())
        Path(path).write_text(json.dumps({"namespace": self.namespace, "executions": [self.execution], "traces": []}, indent=2))
