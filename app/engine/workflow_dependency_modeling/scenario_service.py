"""Application service for versioned workflow scenarios."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.models import WorkflowScenario
from app.engine.workflow_dependency_modeling.scenario_repository import (
    ScenarioRepository,
    SqlAlchemyScenarioRepository,
)


_default_repository: ScenarioRepository | None = None
_SERVER_FIELDS = {"version", "createdAt", "createdBy"}


def configure_scenario_repository(repository: ScenarioRepository) -> None:
    """Configure API storage without coupling graph research functions to a database."""
    global _default_repository
    _default_repository = repository


def _repository(repository: ScenarioRepository | None) -> ScenarioRepository:
    if repository is not None:
        return repository
    global _default_repository
    if _default_repository is None:
        from app.core.config import get_settings
        from app.core.database import get_engine

        _default_repository = SqlAlchemyScenarioRepository(
            get_engine(get_settings().database_url)
        )
    return _default_repository


def _timestamp(now: datetime | None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Scenario timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def create_scenario(
    data: Mapping[str, Any],
    *,
    actor: str,
    repository: ScenarioRepository | None = None,
    now: datetime | None = None,
) -> WorkflowScenario:
    """Create immutable version 1; version metadata is server-controlled."""
    forbidden = _SERVER_FIELDS & data.keys()
    if forbidden:
        raise ValueError(f"Server-managed fields are not accepted: {', '.join(sorted(forbidden))}")
    payload = {
        **data,
        "version": 1,
        "createdAt": _timestamp(now),
        "createdBy": actor,
        "changeSummary": data.get("changeSummary", "Initial version"),
    }
    return _repository(repository).create(WorkflowScenario.from_mapping(payload))


def update_scenario(
    workflow_id: str,
    changes: Mapping[str, Any],
    *,
    expected_version: int,
    actor: str,
    repository: ScenarioRepository | None = None,
    now: datetime | None = None,
) -> WorkflowScenario:
    """Create the next immutable version after an optimistic concurrency check."""
    if expected_version < 1:
        raise ValueError("expected_version must be positive")
    forbidden = {"workflowId", *_SERVER_FIELDS} & changes.keys()
    if forbidden:
        raise ValueError(f"Immutable fields are not accepted: {', '.join(sorted(forbidden))}")
    repo = _repository(repository)
    current = repo.get(workflow_id)
    payload = {
        **current.to_mapping(),
        **changes,
        "workflowId": workflow_id,
        "version": current.version + 1,
        "createdAt": _timestamp(now),
        "createdBy": actor,
    }
    if not payload.get("changeSummary") or payload["changeSummary"] == current.change_summary:
        raise ValueError("An update requires a new non-empty changeSummary")
    updated = WorkflowScenario.from_mapping(payload)
    return repo.add_version(updated, expected_version)


def get_versioned_scenario(
    workflow_id: str,
    version: int | None = None,
    repository: ScenarioRepository | None = None,
) -> WorkflowScenario:
    return _repository(repository).get(workflow_id, version)


def list_versioned_scenarios(
    repository: ScenarioRepository | None = None,
) -> list[WorkflowScenario]:
    return _repository(repository).list_latest()


def scenario_etag(scenario: WorkflowScenario) -> str:
    return f'"{scenario.workflow_id}:{scenario.version}"'


def parse_scenario_etag(value: str, workflow_id: str) -> int:
    expected_prefix = f'"{workflow_id}:'
    if not value.startswith(expected_prefix) or not value.endswith('"'):
        raise ValueError("If-Match must use the scenario ETag returned by the API")
    raw_version = value[len(expected_prefix):-1]
    if not raw_version.isdigit() or int(raw_version) < 1:
        raise ValueError("If-Match contains an invalid scenario version")
    return int(raw_version)
