"""Application service for the guided, evidence-honest operator workflow."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from app.engine.workflow_dependency_modeling.analysis_session_models import (
    WorkflowAnalysisSession,
)
from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    AnalysisSessionRepository,
    SqlAlchemyAnalysisSessionRepository,
)
from app.engine.workflow_dependency_modeling.auto_discovery import _slugify
from app.engine.workflow_dependency_modeling.trace_source import (
    TraceSourceConfiguration,
)


_default_repository: AnalysisSessionRepository | None = None
_LOOKBACK = re.compile(r"^[1-9][0-9]*[smhdw]$")
_SERVER_FIELDS = {
    "sessionId",
    "workflowId",
    "traceCount",
    "observedServices",
    "serviceEvidence",
    "edges",
    "maturity",
    "nextAction",
    "limitations",
    "requiredActions",
    "createdAt",
    "createdBy",
}


def configure_analysis_session_repository(repository: AnalysisSessionRepository) -> None:
    global _default_repository
    _default_repository = repository


def _repository(repository: AnalysisSessionRepository | None) -> AnalysisSessionRepository:
    if repository is not None:
        return repository
    global _default_repository
    if _default_repository is None:
        from app.core.config import get_settings
        from app.core.database import get_engine

        _default_repository = SqlAlchemyAnalysisSessionRepository(
            get_engine(get_settings().database_url)
        )
    return _default_repository


def _jaeger_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("jaegerUrl is required")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("jaegerUrl must be an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("jaegerUrl cannot contain credentials, a query, or a fragment")
    allowed = {
        item.strip().lower()
        for item in os.getenv(
            "WORKFLOW_JAEGER_ALLOWED_HOSTS", "localhost,127.0.0.1,::1,jaeger"
        ).split(",")
        if item.strip()
    }
    if parsed.hostname.lower() not in allowed:
        raise ValueError(
            "Jaeger host is not approved; configure WORKFLOW_JAEGER_ALLOWED_HOSTS"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _trace_source(data: Mapping[str, Any]) -> TraceSourceConfiguration:
    raw_source = data.get("traceSource")
    legacy_url = data.get("jaegerUrl")
    if raw_source is not None and legacy_url is not None:
        raise ValueError("traceSource and jaegerUrl cannot both be provided")
    if raw_source is None:
        source = TraceSourceConfiguration.from_mapping(
            {
                "type": "jaeger",
                "endpoint": legacy_url,
                "authentication": {"type": "none"},
            }
        )
    else:
        source = TraceSourceConfiguration.from_mapping(raw_source)
    endpoint = _jaeger_url(source.endpoint)
    return TraceSourceConfiguration(
        kind=source.kind,
        endpoint=endpoint,
        authentication=source.authentication,
        secret_env=source.secret_env,
    )


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _timestamp(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("Analysis session timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def start_workflow_analysis(
    data: Mapping[str, Any],
    *,
    actor: str,
    repository: AnalysisSessionRepository | None = None,
    now: datetime | None = None,
) -> WorkflowAnalysisSession:
    forbidden = _SERVER_FIELDS & data.keys()
    if forbidden:
        raise ValueError(
            f"Server-managed fields are not accepted: {', '.join(sorted(forbidden))}"
        )
    trace_source = _trace_source(data)
    environment_id = _text(data, "environmentId")
    entry_service = _text(data, "entryService")
    operation_name = _text(data, "operationName")
    lookback = str(data.get("lookback", "1h")).strip()
    if not _LOOKBACK.fullmatch(lookback):
        raise ValueError("lookback must use a positive duration such as 30m, 1h, or 2d")
    limit = data.get("limit", 1000)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise ValueError("limit must be an integer between 1 and 5000")
    evidence_tier = str(data.get("evidenceTier", "experimental"))
    if evidence_tier not in {"experimental", "production"}:
        raise ValueError("evidenceTier must be experimental or production")

    from app.engine.workflow_dependency_modeling.service import get_live_evidence

    evidence = get_live_evidence(
        entry_service,
        operation_name,
        trace_source.endpoint,
        limit,
        lookback,
    )
    timestamp = _timestamp(now)
    analysis = WorkflowAnalysisSession.from_mapping(
        {
            "sessionId": f"analysis:{uuid4().hex}",
            "environmentId": environment_id,
            "evidenceTier": evidence_tier,
            "traceSource": trace_source.to_mapping(),
            "entryService": entry_service,
            "operationName": operation_name,
            "lookback": lookback,
            "workflowId": _slugify(operation_name),
            "traceCount": evidence["traceCount"],
            "observedServices": evidence["observedServices"],
            "serviceEvidence": evidence["serviceEvidence"],
            "edges": evidence["edges"],
            "maturity": "observed",
            "nextAction": "confirm_success_condition",
            "limitations": [
                "Observed calls show participation, not dependency necessity.",
                "No controlled intervention evidence has been collected.",
            ],
            "requiredActions": [
                "Confirm the business success condition.",
                "Collect a stable baseline execution cohort.",
                "Collect confirmed intervention cohorts for candidate services.",
            ],
            "createdAt": timestamp.isoformat(),
            "createdBy": actor,
        }
    )
    return _repository(repository).create(analysis)


def discover_analysis_workflows(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Discover operations through the same allowlisted connection policy."""
    trace_source = _trace_source(data)
    entry_service = _text(data, "entryService")
    lookback = str(data.get("lookback", "1h")).strip()
    if not _LOOKBACK.fullmatch(lookback):
        raise ValueError("lookback must use a positive duration such as 30m, 1h, or 2d")
    limit = data.get("limit", 1000)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise ValueError("limit must be an integer between 1 and 5000")

    from app.engine.workflow_dependency_modeling.service import discover_live_workflows

    return discover_live_workflows(
        service_name=entry_service,
        jaeger_url=trace_source.endpoint,
        limit=limit,
        lookback=lookback,
    )


def get_workflow_analysis(
    session_id: str,
    repository: AnalysisSessionRepository | None = None,
) -> WorkflowAnalysisSession:
    return _repository(repository).get(session_id)


def list_workflow_analyses(
    repository: AnalysisSessionRepository | None = None,
) -> list[WorkflowAnalysisSession]:
    return _repository(repository).list()
