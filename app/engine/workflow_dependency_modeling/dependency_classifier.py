"""Reference-free frequency classification for Component 1.

Phase 1 intentionally calls these results *candidates*. Trace presence is
observational evidence; it does not prove that a service is logically required
for workflow success. Independent reference data is never accepted here.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from app.engine.workflow_dependency_modeling.interaction_extractor import (
    TraceInteraction,
)


class InsufficientEvidenceError(ValueError):
    """Raised when a prediction is requested without trace interactions."""


class DependencyType(str, Enum):
    """Evidence-based candidate label, not validated business necessity."""

    MANDATORY_CANDIDATE = "MANDATORY_CANDIDATE"
    OPTIONAL_CANDIDATE = "OPTIONAL_CANDIDATE"


@dataclass(frozen=True)
class ServiceClassification:
    service_name: str
    dependency_type: DependencyType
    trace_frequency: float
    observed_trace_count: int


@dataclass
class DependencyClassification:
    workflow_id: str
    mandatory_candidates: list[str]
    optional_candidates: list[str]
    service_classifications: list[ServiceClassification] = field(default_factory=list)

    @property
    def mandatory_services(self) -> list[str]:
        """Compatibility view used only by the legacy shared export adapter."""
        return self.mandatory_candidates

    @property
    def optional_services(self) -> list[str]:
        """Compatibility view used only by the legacy shared export adapter."""
        return self.optional_candidates


def classify_dependencies(
    interactions: list[TraceInteraction],
    workflow_id: str,
    mandatory_threshold: float = 1.0,
) -> DependencyClassification:
    """Create candidates using only observed per-trace service frequency.

    Services at or above ``mandatory_threshold`` become mandatory candidates;
    other observed services become optional candidates. No reference label,
    expected-service list, or expected-edge list is accepted by this function.
    """
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise ValueError("workflow_id must be a non-empty string")
    if isinstance(mandatory_threshold, bool) or not isinstance(
        mandatory_threshold, (int, float)
    ):
        raise ValueError("mandatory_threshold must be numeric")
    if not 0.0 <= float(mandatory_threshold) <= 1.0:
        raise ValueError("mandatory_threshold must be between 0 and 1")
    if not interactions:
        raise InsufficientEvidenceError(
            f"No trace interactions are available for workflow '{workflow_id}'"
        )

    appearances: dict[str, set[str]] = defaultdict(set)
    for interaction in interactions:
        observed_services = set(interaction.observed_services)
        if not observed_services:
            # Compatibility for callers constructing TraceInteraction directly.
            observed_services = {
                service
                for edge in interaction.edges
                for service in (edge.source, edge.target)
            }
        for service in observed_services:
            appearances[service].add(interaction.trace_id)

    if not appearances:
        raise InsufficientEvidenceError(
            f"Trace evidence for workflow '{workflow_id}' contains no observed services"
        )

    total_traces = len(interactions)
    mandatory: list[str] = []
    optional: list[str] = []
    classifications: list[ServiceClassification] = []

    for service, trace_ids in sorted(appearances.items()):
        frequency = len(trace_ids) / total_traces
        if frequency >= mandatory_threshold:
            dependency_type = DependencyType.MANDATORY_CANDIDATE
            mandatory.append(service)
        else:
            dependency_type = DependencyType.OPTIONAL_CANDIDATE
            optional.append(service)
        classifications.append(
            ServiceClassification(
                service_name=service,
                dependency_type=dependency_type,
                trace_frequency=frequency,
                observed_trace_count=len(trace_ids),
            )
        )

    return DependencyClassification(
        workflow_id=workflow_id,
        mandatory_candidates=mandatory,
        optional_candidates=optional,
        service_classifications=classifications,
    )
