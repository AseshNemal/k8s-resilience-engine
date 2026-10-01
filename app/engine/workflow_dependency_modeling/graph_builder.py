"""Assemble the legacy shared WorkflowGraph from internal candidates."""

from __future__ import annotations

from app.engine.workflow_dependency_modeling.dependency_classifier import (
    DependencyClassification,
)
from app.engine.workflow_dependency_modeling.interaction_extractor import ObservedEdge
from app.engine.workflow_dependency_modeling.models import WorkflowScenario
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph


def build_workflow_graph(
    classification: DependencyClassification,
    observed_edges: list[ObservedEdge],
    scenario: WorkflowScenario,
    min_edge_frequency: float = 0.0,
) -> WorkflowGraph:
    """Adapt reference-free candidates to the unchanged team integration schema.

    ``WorkflowGraph.mandatory_services`` and ``optional_services`` retain their
    existing names for Member 3 compatibility. Internally, Phase 1 treats them
    as frequency-based candidates until later validation/freeze gates exist.
    """
    if classification.workflow_id != scenario.workflow_id:
        raise ValueError(
            "Classification workflow ID does not match the scenario workflow ID"
        )
    if not 0.0 <= min_edge_frequency <= 1.0:
        raise ValueError("min_edge_frequency must be between 0 and 1")

    filtered_edges = [
        WorkflowEdge(source=edge.source, target=edge.target)
        for edge in observed_edges
        if edge.frequency >= min_edge_frequency
    ]
    filtered_edges.sort(key=lambda edge: (edge.source, edge.target))

    return WorkflowGraph(
        workflow_id=scenario.workflow_id,
        criticality=scenario.criticality,
        mandatory_services=classification.mandatory_candidates,
        optional_services=classification.optional_candidates,
        edges=filtered_edges,
    )
