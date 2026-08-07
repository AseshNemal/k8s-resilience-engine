"""dependency_classifier.py — Member 1: Workflow Dependency Modeling

Classifies each service observed in trace interactions as MANDATORY,
OPTIONAL, or SUPPORTING using two inputs:

1. **Trace frequency** — the fraction of trace runs in which a service
   was observed (derived from :class:`~interaction_extractor.TraceInteraction`).
2. **Workflow definition** — the manually authored reference that lists
   which services are *expected* to participate in this workflow.

Classification rules
--------------------
- **MANDATORY**  — ``trace_frequency >= mandatory_threshold`` AND the service
  is listed in the workflow definition's ``expectedServices``.
  If the service fails, the workflow fails.
- **OPTIONAL**   — ``0 < trace_frequency < mandatory_threshold`` AND the
  service is in ``expectedServices``.
  The service enhances the workflow but its absence does not cause failure
  (confirmed by traces where the workflow succeeded without it).
- **SUPPORTING** — the service appears in traces but is *not* listed in
  ``expectedServices``.
  Typically a shared platform service (e.g. a logging aggregator or
  sidecar) not explicitly part of this business workflow.

Pipeline position::

    interaction_extractor.extract_interactions()
        -> classify_dependencies()   # this module
        -> graph_builder             # (Part 4)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from app.engine.workflow_dependency_modeling.interaction_extractor import (
    TraceInteraction,
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class DependencyType(str, Enum):
    """Classification label for a service dependency within a workflow."""

    MANDATORY = "MANDATORY"
    """Service must be available for the workflow to succeed."""

    OPTIONAL = "OPTIONAL"
    """Service enhances the workflow but its absence does not cause failure."""

    SUPPORTING = "SUPPORTING"
    """Service is observed in traces but is not part of the workflow
    definition (e.g. a shared platform / infrastructure service)."""


@dataclass
class ServiceClassification:
    """Classification result for one service within a workflow."""

    service_name: str
    dependency_type: DependencyType

    trace_frequency: float
    """Fraction of trace runs in which this service was observed
    (as either a caller or a callee). Range: ``0.0``–``1.0``."""

    in_workflow_definition: bool
    """``True`` if this service appears in the reference
    ``expectedServices`` list of the workflow definition."""


@dataclass
class DependencyClassification:
    """Full dependency classification result for one workflow.

    Contains both the rolled-up service name lists (ready for
    :class:`~app.shared.schemas.workflow.WorkflowGraph` construction) and
    the per-service detail objects for research analysis and evaluation.
    """

    workflow_id: str
    mandatory_services: list[str]
    optional_services: list[str]
    supporting_services: list[str]
    service_classifications: list[ServiceClassification] = field(
        default_factory=list
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def classify_dependencies(
    interactions: list[TraceInteraction],
    workflow_definition: dict,
    mandatory_threshold: float = 1.0,
) -> DependencyClassification:
    """Classify each observed service as MANDATORY, OPTIONAL, or SUPPORTING.

    Algorithm
    ---------
    1. Compute a **trace frequency** per service: the fraction of trace runs
       in which the service appeared as either the source or target of at
       least one edge.
    2. Classify each service using ``mandatory_threshold`` and whether the
       service is listed in ``workflow_definition["expectedServices"]``.

    Args:
        interactions: Per-trace edge lists from
            :func:`~interaction_extractor.extract_interactions`.
        workflow_definition: Reference workflow dict loaded from a
            ``sample_data/workflow_definitions/*.json`` file.
            Must contain ``"workflowId"`` (str) and
            ``"expectedServices"`` (list[str]).
        mandatory_threshold: Services with ``trace_frequency >= this`` are
            classified as ``MANDATORY``. Defaults to ``1.0`` (appears in
            every trace run). Lower this value to tolerate occasional
            missed traces (e.g. ``0.9`` for 90 % presence).

    Returns:
        :class:`DependencyClassification` with sorted ``mandatory_services``,
        ``optional_services``, ``supporting_services`` lists and the full
        per-service ``service_classifications`` list.
    """
    workflow_id: str = workflow_definition.get("workflowId", "unknown")
    expected_services: set[str] = set(
        workflow_definition.get("expectedServices", [])
    )

    # ------------------------------------------------------------------
    # Step 1: compute per-service trace frequency
    #
    # A service is considered "present in trace T" if it appears as the
    # source OR target of at least one edge in TraceInteraction T.
    # ------------------------------------------------------------------
    total_traces = len(interactions)

    # service_name -> set of traceIDs in which it was observed
    appearances: dict[str, set[str]] = defaultdict(set)

    for interaction in interactions:
        for edge in interaction.edges:
            appearances[edge.source].add(interaction.trace_id)
            appearances[edge.target].add(interaction.trace_id)

    service_frequency: dict[str, float] = (
        {
            svc: len(trace_ids) / total_traces
            for svc, trace_ids in appearances.items()
        }
        if total_traces > 0
        else {}
    )

    # ------------------------------------------------------------------
    # Step 2: classify each observed service
    # ------------------------------------------------------------------
    classifications: list[ServiceClassification] = []
    mandatory: list[str] = []
    optional: list[str] = []
    supporting: list[str] = []

    for service, frequency in sorted(service_frequency.items()):
        in_definition = service in expected_services

        if not in_definition:
            dep_type = DependencyType.SUPPORTING
            supporting.append(service)
        elif frequency >= mandatory_threshold:
            dep_type = DependencyType.MANDATORY
            mandatory.append(service)
        else:
            dep_type = DependencyType.OPTIONAL
            optional.append(service)

        classifications.append(
            ServiceClassification(
                service_name=service,
                dependency_type=dep_type,
                trace_frequency=frequency,
                in_workflow_definition=in_definition,
            )
        )

    mandatory.sort()
    optional.sort()
    supporting.sort()

    return DependencyClassification(
        workflow_id=workflow_id,
        mandatory_services=mandatory,
        optional_services=optional,
        supporting_services=supporting,
        service_classifications=classifications,
    )
