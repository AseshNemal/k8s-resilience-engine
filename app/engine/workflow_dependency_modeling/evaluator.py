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
    jaccard_similarity: float
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    evaluated_count: int
    accuracy_definition: str


@dataclass
class GraphEvaluationResult:
    workflow_id: str
    edge_metrics: EvaluationMetrics
    service_metrics: EvaluationMetrics
    mandatory_service_metrics: EvaluationMetrics
    mandatory_edge_metrics: EvaluationMetrics | None
    mandatory_edge_evaluation_status: str
    path_metrics: EvaluationMetrics
    workflow_reconstruction_accuracy: float
    graph_processing_time_ms: float = 0.0
    true_positive_edges: list[tuple[str, str]] = field(default_factory=list)
    false_positive_edges: list[tuple[str, str]] = field(default_factory=list)
    false_negative_edges: list[tuple[str, str]] = field(default_factory=list)
    true_positive_services: list[str] = field(default_factory=list)
    false_positive_services: list[str] = field(default_factory=list)
    false_negative_services: list[str] = field(default_factory=list)
    true_positive_mandatory_services: list[str] = field(default_factory=list)
    false_positive_mandatory_services: list[str] = field(default_factory=list)
    false_negative_mandatory_services: list[str] = field(default_factory=list)
    true_positive_mandatory_edges: list[tuple[str, str]] = field(default_factory=list)
    false_positive_mandatory_edges: list[tuple[str, str]] = field(default_factory=list)
    false_negative_mandatory_edges: list[tuple[str, str]] = field(default_factory=list)


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


def _compute_metrics(
    predicted: set,
    reference: set,
    *,
    candidate_universe: set | None = None,
) -> EvaluationMetrics:
    tp = len(predicted & reference)
    fp = len(predicted - reference)
    fn = len(reference - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    jaccard = tp / (tp + fp + fn) if tp + fp + fn else 0.0
    if candidate_universe is None:
        # Compatibility for set-reconstruction metrics that have no independently
        # labelled negatives. The explicit definition prevents this value from
        # being mistaken for confusion-matrix accuracy.
        tn = 0
        evaluated_count = tp + fp + fn
        accuracy = jaccard
        accuracy_definition = "jaccard_no_negative_universe"
    else:
        if not reference <= candidate_universe:
            raise ValueError("Reference positives must be in the candidate universe")
        evaluated_predictions = predicted & candidate_universe
        fp = len(evaluated_predictions - reference)
        unknown_predictions = predicted - candidate_universe
        if unknown_predictions:
            fp += len(unknown_predictions)
        tn = len(candidate_universe - reference - evaluated_predictions)
        evaluated_count = tp + fp + fn + tn
        accuracy = (tp + tn) / evaluated_count if evaluated_count else 0.0
        accuracy_definition = "confusion_matrix_over_explicit_universe"
    return EvaluationMetrics(
        precision,
        recall,
        f1,
        accuracy,
        jaccard,
        tp,
        fp,
        fn,
        tn,
        evaluated_count,
        accuracy_definition,
    )


def _root_to_leaf_paths(
    edges: set[tuple[str, str]],
) -> set[tuple[str, ...]]:
    """Return deterministic simple source-to-sink paths for a directed graph.

    Cyclic graphs have no source-to-sink workflow path under this definition;
    they therefore produce no paths instead of permitting unbounded traversal.
    """
    if not edges:
        return set()
    nodes = {name for edge in edges for name in edge}
    targets = {target for _, target in edges}
    roots = sorted(nodes - targets)
    if not roots:
        return set()
    adjacency: dict[str, set[str]] = {node: set() for node in nodes}
    for source, target in edges:
        adjacency[source].add(target)

    paths: set[tuple[str, ...]] = set()

    def visit(node: str, path: tuple[str, ...]) -> None:
        children = sorted(adjacency[node] - set(path))
        if not children:
            if len(path) > 1:
                paths.add(path)
            return
        for child in children:
            visit(child, (*path, child))

    for root in roots:
        visit(root, (root,))
    return paths


def evaluate_graph(
    generated: WorkflowGraph,
    reference: ReferenceWorkflow,
    *,
    predicted_mandatory_edges: set[tuple[str, str]] | None = None,
    graph_processing_time_ms: float = 0.0,
) -> GraphEvaluationResult:
    """Compare a completed prediction with a separately loaded reference."""
    if generated.workflow_id != reference.workflow_id:
        raise ValueError("Generated graph and reference workflow IDs do not match")
    if graph_processing_time_ms < 0:
        raise ValueError("Graph processing time cannot be negative")

    predicted_edges = {(edge.source, edge.target) for edge in generated.edges}
    reference_edges = {(edge.source, edge.target) for edge in reference.expected_edges}
    predicted_services = set(
        generated.mandatory_services + generated.optional_services
    )
    reference_services = set(reference.expected_services)
    predicted_mandatory_services = set(generated.mandatory_services)
    reference_mandatory_services = set(reference.mandatory_services)
    if predicted_mandatory_edges is not None:
        unknown_edges = predicted_mandatory_edges - predicted_edges
        if unknown_edges:
            raise ValueError(
                "Predicted mandatory edges must be present in the generated graph"
            )
    reference_mandatory_edges = {
        (edge.source, edge.target) for edge in reference.mandatory_edges
    }
    edge_universe = {
        (edge.source, edge.target) for edge in reference.evaluated_edge_universe
    }
    service_universe = set(reference.expected_services)
    predicted_paths = _root_to_leaf_paths(predicted_edges)
    reference_paths = _root_to_leaf_paths(reference_edges)

    return GraphEvaluationResult(
        workflow_id=generated.workflow_id,
        edge_metrics=_compute_metrics(
            predicted_edges,
            reference_edges,
            candidate_universe=edge_universe or None,
        ),
        service_metrics=_compute_metrics(predicted_services, reference_services),
        mandatory_service_metrics=_compute_metrics(
            predicted_mandatory_services,
            reference_mandatory_services,
            candidate_universe=service_universe,
        ),
        mandatory_edge_metrics=(
            _compute_metrics(
                predicted_mandatory_edges,
                reference_mandatory_edges,
                candidate_universe=edge_universe or reference_edges,
            )
            if predicted_mandatory_edges is not None
            else None
        ),
        mandatory_edge_evaluation_status=(
            "evaluated"
            if predicted_mandatory_edges is not None
            else "not_evaluated_no_predicted_edge_labels"
        ),
        path_metrics=_compute_metrics(predicted_paths, reference_paths),
        workflow_reconstruction_accuracy=float(
            predicted_services == reference_services
            and predicted_edges == reference_edges
        ),
        graph_processing_time_ms=graph_processing_time_ms,
        true_positive_edges=sorted(predicted_edges & reference_edges),
        false_positive_edges=sorted(predicted_edges - reference_edges),
        false_negative_edges=sorted(reference_edges - predicted_edges),
        true_positive_services=sorted(predicted_services & reference_services),
        false_positive_services=sorted(predicted_services - reference_services),
        false_negative_services=sorted(reference_services - predicted_services),
        true_positive_mandatory_services=sorted(
            predicted_mandatory_services & reference_mandatory_services
        ),
        false_positive_mandatory_services=sorted(
            predicted_mandatory_services - reference_mandatory_services
        ),
        false_negative_mandatory_services=sorted(
            reference_mandatory_services - predicted_mandatory_services
        ),
        true_positive_mandatory_edges=sorted(
            (predicted_mandatory_edges or set()) & reference_mandatory_edges
        ),
        false_positive_mandatory_edges=sorted(
            (predicted_mandatory_edges or set()) - reference_mandatory_edges
        ),
        false_negative_mandatory_edges=sorted(
            reference_mandatory_edges - (predicted_mandatory_edges or set())
            if predicted_mandatory_edges is not None
            else set()
        ),
    )


def run_sensitivity_analysis(
    workflow_id: str,
    thresholds: list[float] | None = None,
) -> list[SensitivityStep]:
    """Evaluate mandatory-label reconstruction across fixture thresholds.

    This compatibility helper is limited to synthetic fixture diagnostics. New
    empirical experiments should use ``run_dependency_sensitivity_analysis``
    against a frozen dependency assessment.
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
                precision=result.mandatory_service_metrics.precision,
                recall=result.mandatory_service_metrics.recall,
                f1=result.mandatory_service_metrics.f1,
                edge_accuracy=result.edge_metrics.accuracy,
                mandatory_services_count=len(graph.mandatory_services),
                optional_services_count=len(graph.optional_services),
                mandatory_services=graph.mandatory_services,
                optional_services=graph.optional_services,
            )
        )
    return steps
