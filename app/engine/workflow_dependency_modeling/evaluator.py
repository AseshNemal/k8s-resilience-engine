"""evaluator.py — Member 1: Workflow Dependency Modeling

Evaluates the quality of a generated :class:`~app.shared.schemas.workflow.WorkflowGraph`
by comparing it against the manually authored reference workflow definition.

Metrics computed
----------------
For both **edge-level** and **service-level** comparisons:

- **Precision**: fraction of predicted items that are correct.
- **Recall**: fraction of reference items that were successfully predicted.
- **F1-score**: harmonic mean of precision and recall.
- **Accuracy** (Jaccard index): ``TP / (TP + FP + FN)`` — fraction of the
  union of predicted and reference that is correct.

These metrics directly address the research evaluation question:
*"How accurately does the system identify the true mandatory service
dependencies?"*

Pipeline position::

    graph_builder.build_workflow_graph()   → WorkflowGraph
        -> evaluate_graph()                # this module
        -> GraphEvaluationResult           # research measurement output
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.shared.schemas.workflow import WorkflowGraph


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class EvaluationMetrics:
    """Standard information-retrieval metrics for one comparison axis."""

    precision: float
    """TP / (TP + FP). Fraction of predicted items that are correct."""

    recall: float
    """TP / (TP + FN). Fraction of reference items that were predicted."""

    f1: float
    """Harmonic mean of precision and recall: 2·P·R / (P + R)."""

    accuracy: float
    """Jaccard similarity: TP / (TP + FP + FN).
    Called *edge-level accuracy* in the research spec."""

    true_positives: int
    false_positives: int
    false_negatives: int


@dataclass
class GraphEvaluationResult:
    """Evaluation result for one generated :class:`WorkflowGraph`.

    Provides per-axis metric objects plus itemised true-positive,
    false-positive, and false-negative lists for qualitative analysis.
    """

    workflow_id: str

    # Aggregated metric objects -----------------------------------------
    edge_metrics: EvaluationMetrics
    """Precision / recall / F1 / accuracy comparing generated graph edges
    against ``workflow_definition["expectedEdges"]``."""

    service_metrics: EvaluationMetrics
    """Precision / recall / F1 / accuracy comparing predicted services
    (mandatory + optional) against ``workflow_definition["expectedServices"]``."""

    # Itemised edge results ---------------------------------------------
    true_positive_edges: list[tuple[str, str]] = field(default_factory=list)
    """Edges present in both the generated graph and the reference."""

    false_positive_edges: list[tuple[str, str]] = field(default_factory=list)
    """Edges in the generated graph that are absent from the reference
    (unexpected edges discovered from traces)."""

    false_negative_edges: list[tuple[str, str]] = field(default_factory=list)
    """Edges in the reference that the generated graph missed."""

    # Itemised service results ------------------------------------------
    true_positive_services: list[str] = field(default_factory=list)
    """Services correctly predicted."""

    false_positive_services: list[str] = field(default_factory=list)
    """Services predicted but not in the reference expected list."""

    false_negative_services: list[str] = field(default_factory=list)
    """Services in the reference that were not predicted."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_metrics(
    predicted: set,
    reference: set,
) -> EvaluationMetrics:
    """Compute precision, recall, F1, and Jaccard accuracy for two sets."""
    tp = len(predicted & reference)
    fp = len(predicted - reference)
    fn = len(reference - predicted)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    accuracy = tp / (tp + fp + fn) if (tp + fp + fn) > 0 else 0.0

    return EvaluationMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        accuracy=accuracy,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def evaluate_graph(
    generated: WorkflowGraph,
    workflow_definition: dict,
) -> GraphEvaluationResult:
    """Evaluate a generated :class:`WorkflowGraph` against a reference definition.

    Args:
        generated: The :class:`WorkflowGraph` produced by
            :func:`~graph_builder.build_workflow_graph`.
        workflow_definition: Reference workflow dict from
            ``sample_data/workflow_definitions/*.json``.
            Must contain ``"expectedEdges"`` (list of
            ``{"source": str, "target": str}``) and
            ``"expectedServices"`` (list of str).

    Returns:
        :class:`GraphEvaluationResult` with edge-level and service-level
        metric objects plus itemised TP / FP / FN lists.
    """
    # ------------------------------------------------------------------
    # Edge-level evaluation
    # ------------------------------------------------------------------
    predicted_edges: set[tuple[str, str]] = {
        (e.source, e.target) for e in generated.edges
    }
    reference_edges: set[tuple[str, str]] = {
        (e["source"], e["target"])
        for e in workflow_definition.get("expectedEdges", [])
    }

    edge_metrics = _compute_metrics(predicted_edges, reference_edges)

    tp_edges = sorted(predicted_edges & reference_edges)
    fp_edges = sorted(predicted_edges - reference_edges)
    fn_edges = sorted(reference_edges - predicted_edges)

    # ------------------------------------------------------------------
    # Service-level evaluation
    #
    # Predicted = mandatory + optional (supporting services are explicitly
    # not part of this workflow definition, so we exclude them).
    # Reference = expectedServices from the workflow definition.
    # ------------------------------------------------------------------
    predicted_services: set[str] = set(
        generated.mandatory_services + generated.optional_services
    )
    reference_services: set[str] = set(
        workflow_definition.get("expectedServices", [])
    )

    service_metrics = _compute_metrics(predicted_services, reference_services)

    tp_svcs = sorted(predicted_services & reference_services)
    fp_svcs = sorted(predicted_services - reference_services)
    fn_svcs = sorted(reference_services - predicted_services)

    return GraphEvaluationResult(
        workflow_id=generated.workflow_id,
        edge_metrics=edge_metrics,
        service_metrics=service_metrics,
        true_positive_edges=tp_edges,
        false_positive_edges=fp_edges,
        false_negative_edges=fn_edges,
        true_positive_services=tp_svcs,
        false_positive_services=fp_svcs,
        false_negative_services=fn_svcs,
    )
