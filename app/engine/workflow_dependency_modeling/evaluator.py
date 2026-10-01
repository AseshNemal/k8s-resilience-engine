"""Evaluation-only comparison of predictions with independent references."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.engine.workflow_dependency_modeling.models import ReferenceWorkflow
from app.shared.schemas.workflow import WorkflowGraph


@dataclass(frozen=True)
class EvaluationMetrics:
    precision: float
    recall: float
    f1: float
    accuracy: float
    true_positives: int
    false_positives: int
    false_negatives: int


@dataclass
class GraphEvaluationResult:
    workflow_id: str
    edge_metrics: EvaluationMetrics
    service_metrics: EvaluationMetrics
    true_positive_edges: list[tuple[str, str]] = field(default_factory=list)
    false_positive_edges: list[tuple[str, str]] = field(default_factory=list)
    false_negative_edges: list[tuple[str, str]] = field(default_factory=list)
    true_positive_services: list[str] = field(default_factory=list)
    false_positive_services: list[str] = field(default_factory=list)
    false_negative_services: list[str] = field(default_factory=list)


@dataclass
class SensitivityStep:
    threshold: float
    precision: float
    recall: float
    f1: float
    edge_accuracy: float
    mandatory_services_count: int
    optional_services_count: int
    mandatory_services: list[str] = field(default_factory=list)
    optional_services: list[str] = field(default_factory=list)


def _compute_metrics(predicted: set, reference: set) -> EvaluationMetrics:
    tp = len(predicted & reference)
    fp = len(predicted - reference)
    fn = len(reference - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = tp / (tp + fp + fn) if tp + fp + fn else 0.0
    return EvaluationMetrics(precision, recall, f1, accuracy, tp, fp, fn)


def evaluate_graph(
    generated: WorkflowGraph,
    reference: ReferenceWorkflow,
) -> GraphEvaluationResult:
    """Compare a completed prediction with a separately loaded reference."""
    if generated.workflow_id != reference.workflow_id:
        raise ValueError("Generated graph and reference workflow IDs do not match")

    predicted_edges = {(edge.source, edge.target) for edge in generated.edges}
    reference_edges = {(edge.source, edge.target) for edge in reference.expected_edges}
    predicted_services = set(
        generated.mandatory_services + generated.optional_services
    )
    reference_services = set(reference.expected_services)

    return GraphEvaluationResult(
        workflow_id=generated.workflow_id,
        edge_metrics=_compute_metrics(predicted_edges, reference_edges),
        service_metrics=_compute_metrics(predicted_services, reference_services),
        true_positive_edges=sorted(predicted_edges & reference_edges),
        false_positive_edges=sorted(predicted_edges - reference_edges),
        false_negative_edges=sorted(reference_edges - predicted_edges),
        true_positive_services=sorted(predicted_services & reference_services),
        false_positive_services=sorted(predicted_services - reference_services),
        false_negative_services=sorted(reference_services - predicted_services),
    )


def run_sensitivity_analysis(
    workflow_id: str,
    thresholds: list[float] | None = None,
) -> list[SensitivityStep]:
    """Evaluate fixture set reconstruction across frequency thresholds.

    Phase 1 retains the legacy combined-service metric. Correct mandatory-label
    sensitivity is explicitly Phase 5 work and is not claimed here.
    """
    from app.engine.workflow_dependency_modeling.fixture_repository import get_reference
    from app.engine.workflow_dependency_modeling.service import get_workflow_by_id

    selected = thresholds or [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0]
    reference = get_reference(workflow_id)
    steps: list[SensitivityStep] = []
    for threshold in sorted(selected):
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("Sensitivity thresholds must be between 0 and 1")
        graph = get_workflow_by_id(workflow_id, mandatory_threshold=threshold)
        result = evaluate_graph(graph, reference)
        steps.append(
            SensitivityStep(
                threshold=threshold,
                precision=result.service_metrics.precision,
                recall=result.service_metrics.recall,
                f1=result.service_metrics.f1,
                edge_accuracy=result.edge_metrics.accuracy,
                mandatory_services_count=len(graph.mandatory_services),
                optional_services_count=len(graph.optional_services),
                mandatory_services=graph.mandatory_services,
                optional_services=graph.optional_services,
            )
        )
    return steps
