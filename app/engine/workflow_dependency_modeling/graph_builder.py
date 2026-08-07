"""graph_builder.py — Member 1: Workflow Dependency Modeling

Assembles a validated :class:`~app.shared.schemas.workflow.WorkflowGraph`
from the three upstream outputs:

1. :class:`~dependency_classifier.DependencyClassification` — mandatory,
   optional, and supporting service lists.
2. :class:`~interaction_extractor.ObservedEdge` list — aggregated directed
   service edges with frequencies.
3. ``workflow_definition`` dict — provides criticality and the workflow ID.

This is the final assembly step before the graph is exported to Member 3
(Failure Impact Prediction).

Pipeline position::

    dependency_classifier.classify_dependencies()
    interaction_extractor.aggregate_edges()
    workflow_definition (JSON)
        -> build_workflow_graph()   # this module
        -> WorkflowGraph            # shared contract for Member 3
"""

from __future__ import annotations

from app.engine.workflow_dependency_modeling.dependency_classifier import (
    DependencyClassification,
)
from app.engine.workflow_dependency_modeling.interaction_extractor import (
    ObservedEdge,
)
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def build_workflow_graph(
    classification: DependencyClassification,
    observed_edges: list[ObservedEdge],
    workflow_definition: dict,
    min_edge_frequency: float = 0.0,
) -> WorkflowGraph:
    """Assemble a :class:`WorkflowGraph` from classified dependencies and edges.

    Args:
        classification: Output of
            :func:`~dependency_classifier.classify_dependencies` — provides
            ``mandatory_services`` and ``optional_services``.
        observed_edges: Output of
            :func:`~interaction_extractor.aggregate_edges` — provides the
            directed service call edges with occurrence frequencies.
        workflow_definition: Reference workflow dict from
            ``sample_data/workflow_definitions/*.json``. Must contain
            ``"criticality"`` (float). ``"workflowId"`` is taken from
            *classification.workflow_id*.
        min_edge_frequency: Only edges whose ``frequency >= min_edge_frequency``
            are included in the graph. Defaults to ``0.0`` (include all
            observed edges). Set to ``1.0`` to include only mandatory edges
            (those present in every trace run).

    Returns:
        A validated :class:`~app.shared.schemas.workflow.WorkflowGraph`
        ready to be returned by the API and consumed by Member 3.
    """
    criticality: float = float(workflow_definition.get("criticality", 0.5))

    # Filter edges by frequency threshold --------------------------------
    filtered_edges: list[WorkflowEdge] = [
        WorkflowEdge(source=e.source, target=e.target)
        for e in observed_edges
        if e.frequency >= min_edge_frequency
    ]

    # Sort edges for stable, deterministic output -------------------------
    filtered_edges.sort(key=lambda e: (e.source, e.target))

    return WorkflowGraph(
        workflow_id=classification.workflow_id,
        criticality=criticality,
        mandatory_services=classification.mandatory_services,
        optional_services=classification.optional_services,
        edges=filtered_edges,
    )
