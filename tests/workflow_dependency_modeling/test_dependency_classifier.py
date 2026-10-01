"""Reference-free candidate-classifier tests."""

import pytest

from app.engine.workflow_dependency_modeling.dependency_classifier import (
    DependencyType,
    InsufficientEvidenceError,
    classify_dependencies,
)
from app.engine.workflow_dependency_modeling.interaction_extractor import (
    ServiceEdge,
    TraceInteraction,
)


def interactions() -> list[TraceInteraction]:
    return [
        TraceInteraction(
            trace_id="t1",
            edges=[
                ServiceEdge(source="frontend", target="checkout"),
                ServiceEdge(source="checkout", target="email"),
            ],
        ),
        TraceInteraction(
            trace_id="t2",
            edges=[ServiceEdge(source="frontend", target="checkout")],
        ),
    ]


def test_frequency_creates_mandatory_and_optional_candidates() -> None:
    result = classify_dependencies(interactions(), "checkout")

    assert result.mandatory_candidates == ["checkout", "frontend"]
    assert result.optional_candidates == ["email"]
    by_name = {item.service_name: item for item in result.service_classifications}
    assert by_name["frontend"].dependency_type is DependencyType.MANDATORY_CANDIDATE
    assert by_name["email"].dependency_type is DependencyType.OPTIONAL_CANDIDATE
    assert by_name["email"].observed_trace_count == 1
    assert by_name["email"].trace_frequency == pytest.approx(0.5)


def test_lower_threshold_promotes_observed_candidate() -> None:
    result = classify_dependencies(
        interactions(), "checkout", mandatory_threshold=0.5
    )
    assert result.mandatory_candidates == ["checkout", "email", "frontend"]
    assert result.optional_candidates == []


@pytest.mark.parametrize("threshold", [-0.01, 1.01])
def test_invalid_threshold_is_rejected(threshold: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        classify_dependencies(interactions(), "checkout", threshold)


def test_missing_evidence_is_not_silently_classified() -> None:
    with pytest.raises(InsufficientEvidenceError, match="No trace interactions"):
        classify_dependencies([], "checkout")


def test_trace_without_cross_service_edges_is_not_silently_classified() -> None:
    with pytest.raises(InsufficientEvidenceError, match="no cross-service"):
        classify_dependencies([TraceInteraction(trace_id="t1", edges=[])], "checkout")


def test_classifier_interface_has_no_reference_argument() -> None:
    with pytest.raises(ValueError, match="workflow_id"):
        classify_dependencies(interactions(), {"workflowId": "checkout"})  # type: ignore[arg-type]
