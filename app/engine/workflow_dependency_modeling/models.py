"""Internal Component 1 models with explicit research-data boundaries.

These models deliberately do not extend the shared ``WorkflowGraph`` contract.
Scenarios describe what should be exercised, evidence identifies what was
observed, and references are available only to evaluation code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping


class ComponentDataError(ValueError):
    """Raised when Component 1 data violates an internal contract."""


def _required_text(data: Mapping[str, Any], key: str, kind: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ComponentDataError(f"{kind} requires a non-empty '{key}' string")
    return value.strip()


@dataclass(frozen=True)
class WorkflowScenario:
    """Business input known before trace analysis.

    Reference labels and evidence file locations are intentionally forbidden.
    Phase 2 will replace the textual success condition with a structured model.
    """

    workflow_id: str
    description: str
    criticality: float
    success_condition: str
    entry_service: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "WorkflowScenario":
        forbidden = {"expectedServices", "expectedEdges", "traceFile"} & data.keys()
        if forbidden:
            names = ", ".join(sorted(forbidden))
            raise ComponentDataError(
                f"WorkflowScenario cannot contain reference/evidence fields: {names}"
            )
        criticality = data.get("criticality")
        if not isinstance(criticality, (int, float)) or isinstance(criticality, bool):
            raise ComponentDataError("WorkflowScenario requires numeric 'criticality'")
        if not 0.0 <= float(criticality) <= 1.0:
            raise ComponentDataError("WorkflowScenario criticality must be between 0 and 1")
        return cls(
            workflow_id=_required_text(data, "workflowId", "WorkflowScenario"),
            description=_required_text(data, "description", "WorkflowScenario"),
            criticality=float(criticality),
            success_condition=_required_text(
                data, "successCondition", "WorkflowScenario"
            ),
            entry_service=_required_text(data, "entryService", "WorkflowScenario"),
        )


@dataclass(frozen=True)
class WorkflowEvidenceSource:
    """Descriptor for observed trace evidence; it contains no answer labels."""

    workflow_id: str
    source_kind: str
    trace_file: str
    synthetic: bool

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "WorkflowEvidenceSource":
        workflow_id = _required_text(data, "workflowId", "WorkflowEvidenceSource")
        source_kind = _required_text(data, "sourceKind", "WorkflowEvidenceSource")
        trace_file = _required_text(data, "traceFile", "WorkflowEvidenceSource")
        path = PurePosixPath(trace_file)
        if path.is_absolute() or ".." in path.parts:
            raise ComponentDataError("WorkflowEvidenceSource traceFile must be repository-relative")
        synthetic = data.get("synthetic")
        if not isinstance(synthetic, bool):
            raise ComponentDataError("WorkflowEvidenceSource requires boolean 'synthetic'")
        return cls(workflow_id, source_kind, trace_file, synthetic)


@dataclass(frozen=True, order=True)
class ReferenceEdge:
    source: str
    target: str


@dataclass(frozen=True)
class ReferenceWorkflow:
    """Evaluation-only expected service and edge sets."""

    workflow_id: str
    expected_services: tuple[str, ...]
    expected_edges: tuple[ReferenceEdge, ...]
    synthetic: bool

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ReferenceWorkflow":
        workflow_id = _required_text(data, "workflowId", "ReferenceWorkflow")
        if "expectedServices" not in data or "expectedEdges" not in data:
            raise ComponentDataError(
                "ReferenceWorkflow requires expectedServices and expectedEdges"
            )
        raw_services = data["expectedServices"]
        raw_edges = data["expectedEdges"]
        if not isinstance(raw_services, list) or not all(
            isinstance(item, str) and item.strip() for item in raw_services
        ):
            raise ComponentDataError("ReferenceWorkflow expectedServices must be strings")
        if not isinstance(raw_edges, list):
            raise ComponentDataError("ReferenceWorkflow expectedEdges must be a list")
        edges: list[ReferenceEdge] = []
        for item in raw_edges:
            if not isinstance(item, Mapping):
                raise ComponentDataError("Each reference edge must be an object")
            edges.append(
                ReferenceEdge(
                    source=_required_text(item, "source", "ReferenceEdge"),
                    target=_required_text(item, "target", "ReferenceEdge"),
                )
            )
        synthetic = data.get("synthetic")
        if not isinstance(synthetic, bool):
            raise ComponentDataError("ReferenceWorkflow requires boolean 'synthetic'")
        return cls(
            workflow_id=workflow_id,
            expected_services=tuple(sorted(set(raw_services))),
            expected_edges=tuple(sorted(set(edges))),
            synthetic=synthetic,
        )
