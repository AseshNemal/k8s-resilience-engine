"""Immutable provenance record for a Component 1 graph handoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.shared.schemas.workflow import WorkflowGraph


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ComponentDataError(f"Frozen workflow graph requires non-empty '{key}'")
    return value.strip()


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ComponentDataError("Frozen workflow graph timestamp must be ISO 8601")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ComponentDataError("Frozen workflow graph timestamp must be ISO 8601") from err
    if result.tzinfo is None:
        raise ComponentDataError("Frozen workflow graph timestamp must include a timezone")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class FrozenWorkflowGraph:
    freeze_id: str
    workflow_id: str
    assessment_id: str
    scenario_version: int
    graph: WorkflowGraph
    trace_ids: tuple[str, ...]
    valid_trace_count: int
    excluded_trace_count: int
    algorithm_version: str
    mandatory_threshold: float
    frozen_at: datetime
    frozen_by: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "freezeId": self.freeze_id,
            "workflowId": self.workflow_id,
            "assessmentId": self.assessment_id,
            "scenarioVersion": self.scenario_version,
            "graph": self.graph.model_dump(by_alias=True),
            "traceIds": list(self.trace_ids),
            "validTraceCount": self.valid_trace_count,
            "excludedTraceCount": self.excluded_trace_count,
            "algorithmVersion": self.algorithm_version,
            "mandatoryThreshold": self.mandatory_threshold,
            "frozenAt": self.frozen_at.isoformat().replace("+00:00", "Z"),
            "frozenBy": self.frozen_by,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "FrozenWorkflowGraph":
        version = data.get("scenarioVersion")
        valid_count = data.get("validTraceCount")
        excluded_count = data.get("excludedTraceCount")
        threshold = data.get("mandatoryThreshold")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ComponentDataError("scenarioVersion must be a positive integer")
        for name, value in (
            ("validTraceCount", valid_count),
            ("excludedTraceCount", excluded_count),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ComponentDataError(f"{name} must be a non-negative integer")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ComponentDataError("mandatoryThreshold must be numeric")
        if not 0.0 <= float(threshold) <= 1.0:
            raise ComponentDataError("mandatoryThreshold must be between 0 and 1")
        raw_trace_ids = data.get("traceIds")
        if not isinstance(raw_trace_ids, list):
            raise ComponentDataError("traceIds must be a list")
        trace_ids = tuple(str(item).strip() for item in raw_trace_ids)
        if any(not item for item in trace_ids) or len(trace_ids) != len(set(trace_ids)):
            raise ComponentDataError("traceIds must be unique non-empty strings")
        if valid_count != len(trace_ids):
            raise ComponentDataError("validTraceCount must equal the number of traceIds")
        workflow_id = _required_text(data, "workflowId")
        graph = WorkflowGraph.model_validate(data.get("graph"))
        if graph.workflow_id != workflow_id:
            raise ComponentDataError(
                "Frozen workflow graph workflowId must match the embedded graph"
            )
        return cls(
            freeze_id=_required_text(data, "freezeId"),
            workflow_id=workflow_id,
            assessment_id=_required_text(data, "assessmentId"),
            scenario_version=version,
            graph=graph,
            trace_ids=trace_ids,
            valid_trace_count=valid_count,
            excluded_trace_count=excluded_count,
            algorithm_version=_required_text(data, "algorithmVersion"),
            mandatory_threshold=float(threshold),
            frozen_at=_timestamp(data.get("frozenAt")),
            frozen_by=_required_text(data, "frozenBy"),
        )
