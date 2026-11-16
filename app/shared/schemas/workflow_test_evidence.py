"""Application-test evidence, never arbitrary test-file execution or experimental proof."""
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, model_validator
from app.shared.schemas.base import APIModel


class TestEvidenceModel(APIModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, alias_generator=APIModel.model_config["alias_generator"])


class TestAssertion(TestEvidenceModel):
    name: str = Field(min_length=1, max_length=160)
    passed: bool


class DependencyDeclaration(TestEvidenceModel):
    source: str = Field(min_length=1, max_length=253)
    target: str = Field(min_length=1, max_length=253)
    dependency: Literal["Mandatory", "Optional", "Supporting", "Unknown"]


class TestExecutionEvidence(TestEvidenceModel):
    test_name: str = Field(min_length=1, max_length=160)
    execution_id: str = Field(min_length=1, max_length=160)
    runner: Literal["pytest", "playwright", "http_probe", "external"] = "external"
    application_url: str = Field(min_length=1, max_length=2048)
    entry_service: str = Field(min_length=1, max_length=253)
    started_at: datetime
    finished_at: datetime
    trace_ids: list[str] = Field(default_factory=list, max_length=20)
    outcome_trace_id: str | None = None
    reported_status: Literal["passed", "failed", "skipped", "unknown"] = "unknown"
    assertions: list[TestAssertion] = Field(default_factory=list, max_length=100)
    success_condition: dict[str, Any] | None = None
    dependency_declarations: list[DependencyDeclaration] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def execution_contract(self):
        import re
        url = urlsplit(self.application_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("applicationUrl must be HTTP(S), without credentials, query or fragment")
        if not self.test_name.strip() or not self.execution_id.strip():
            raise ValueError("Test name and execution identity must contain text")
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None or self.finished_at < self.started_at:
            raise ValueError("Each test needs ordered, timezone-aware start and finish timestamps")
        if len(set(self.trace_ids)) != len(self.trace_ids) or any(not re.fullmatch(r"[0-9a-f]{32}", i) or int(i, 16) == 0 for i in self.trace_ids):
            raise ValueError("Each request needs a distinct, nonzero, 32-character OpenTelemetry trace ID")
        if self.outcome_trace_id and self.outcome_trace_id not in self.trace_ids:
            raise ValueError("outcomeTraceId must belong to this execution")
        return self


class TestEvidenceImport(TestEvidenceModel):
    namespace: str = Field(min_length=1, max_length=63)
    connection_fingerprint: str = Field(min_length=1, max_length=200)
    executions: list[TestExecutionEvidence] = Field(min_length=1, max_length=10)
    traces: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    jaeger_url: str | None = Field(default=None, max_length=2048)
    service_map: dict[str, str] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def unique_executions(self):
        import json
        if len(json.dumps(self.model_dump(mode="json")).encode()) > 8 * 1024 * 1024:
            raise ValueError("Test evidence exceeds 8 MiB; export only the selected test executions")
        ids = [e.execution_id for e in self.executions]
        traces = [i for e in self.executions for i in e.trace_ids]
        if len(ids) != len(set(ids)) or len(traces) != len(set(traces)) or len(traces) > 30:
            raise ValueError("Executions must have unique identities and separate trace IDs (30 total maximum)")
        return self


class HttpTestRun(TestEvidenceModel):
    namespace: str = Field(min_length=1, max_length=63)
    connection_fingerprint: str = Field(min_length=1, max_length=200)
    test_name: str = Field(min_length=1, max_length=160)
    application_url: str = Field(min_length=1, max_length=2048)
    entry_service: str = Field(min_length=1, max_length=253)
    execution_definition: dict[str, Any]
    jaeger_url: str = Field(min_length=1, max_length=2048)
    service_map: dict[str, str] = Field(default_factory=dict, max_length=100)
