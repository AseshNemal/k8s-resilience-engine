"""Frozen graph provenance model invariants."""

import copy

import pytest

from app.engine.workflow_dependency_modeling.graph_freeze_models import (
    FrozenWorkflowGraph,
)
from app.engine.workflow_dependency_modeling.models import ComponentDataError


def _mapping() -> dict:
    return {
        "freezeId": "freeze:checkout:1",
        "workflowId": "checkout",
        "assessmentId": "assessment:checkout:1",
        "scenarioVersion": 1,
        "graph": {
            "workflowId": "checkout",
            "criticality": 0.9,
            "mandatoryServices": ["frontend", "paymentservice"],
            "optionalServices": ["emailservice"],
            "edges": [
                {"source": "frontend", "target": "paymentservice"},
            ],
        },
        "traceIds": ["trace:1", "trace:2"],
        "validTraceCount": 2,
        "excludedTraceCount": 1,
        "algorithmVersion": "workflow-dependency-modeling/phase-5",
        "mandatoryThreshold": 0.5,
        "frozenAt": "2026-10-07T00:00:00Z",
        "frozenBy": "lead-researcher",
    }


def test_frozen_graph_round_trips_valid_provenance() -> None:
    artifact = FrozenWorkflowGraph.from_mapping(_mapping())

    assert FrozenWorkflowGraph.from_mapping(artifact.to_mapping()) == artifact


def test_frozen_graph_rejects_mismatched_embedded_workflow() -> None:
    payload = copy.deepcopy(_mapping())
    payload["graph"]["workflowId"] = "refund"

    with pytest.raises(ComponentDataError, match="embedded graph"):
        FrozenWorkflowGraph.from_mapping(payload)


def test_frozen_graph_requires_trace_ids_to_be_a_list() -> None:
    payload = _mapping()
    payload["traceIds"] = "trace:1"

    with pytest.raises(ComponentDataError, match="traceIds must be a list"):
        FrozenWorkflowGraph.from_mapping(payload)
