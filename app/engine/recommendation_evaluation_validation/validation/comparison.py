"""Predicted versus observed: service survival, mandatory-workflow survival, blast radius, risk reduction, error.

Pure. The set-level comparison of failed services and workflows is Component 3's own
``compare_prediction_with_observation``; this module adds what is specific to judging a recommendation: the
before/after pair, the risk reduction, the error figures and the verdict under configurable thresholds.

Definitions (all in percentage points of blast radius, Component 3's scale):
* ``predictionError`` is ``|predicted blast radius after - observed blast radius after|``. It is the same quantity the
  shared ``ValidationReport.prediction_error`` carries.
* ``riskReduction`` is blast radius before minus after. Predicted uses the simulation; observed uses the measured
  failure after the change and a baseline whose basis is stated (``MEASURED`` when a failure was injected before the
  change, otherwise ``DERIVED_FROM_PLACEMENT``, which is a prediction and not a measurement).
* A figure that was not measured is ``None``, never zero.
"""

from collections import Counter
from typing import Any

from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
    compare_prediction_with_observation,
)
from app.engine.recommendation_evaluation_validation.validation.models import (
    DERIVED,
    ObservedOutcome,
    PredictedOutcome,
    ValidationThresholds,
)
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.workflow import WorkflowGraph


def workflow_services(workflow: WorkflowGraph) -> list[str]:
    names = set(workflow.mandatory_services) | set(workflow.optional_services)
    for edge in workflow.edges:
        names.update((edge.source, edge.target))
    return sorted(names)


def observed_placement(graph: DeploymentGraph, services: list[str]) -> dict[str, dict[str, int]]:
    wanted = set(services)
    return {s.name: dict(sorted(Counter(p.node for p in s.pods if p.ready).items()))
            for s in graph.services if s.name in wanted}


def compare_outcomes(
    predicted: PredictedOutcome, observed_before: ObservedOutcome, observed_after: ObservedOutcome,
    workflow: WorkflowGraph, thresholds: ValidationThresholds, placed: DeploymentGraph | None = None,
) -> tuple[dict[str, Any], bool, tuple[str, ...], float]:
    """(comparison, validated, reasons the verdict is not validated, prediction error)."""
    after_p, after_o = predicted.after, observed_after.report
    scope = workflow_services(workflow)
    sets = compare_prediction_with_observation(after_p, ObservedFailureOutcome(
        experiment_id=predicted.recommendation_id, failed_node=after_o.failed_node,
        failed_services=tuple(after_o.failed_services), failed_workflows=tuple(after_o.failed_workflows)))

    services = [{"service": name, "predictedSurvives": name not in after_p.failed_services,
                 "observedSurvives": name not in after_o.failed_services,
                 "match": (name in after_p.failed_services) == (name in after_o.failed_services)} for name in scope]
    workflow_predicted = predicted.workflow_id not in after_p.failed_workflows
    workflow_observed = predicted.workflow_id not in after_o.failed_workflows
    error = round(abs(after_p.blast_radius - after_o.blast_radius), 2)
    reduction_observed = round(observed_before.report.blast_radius - after_o.blast_radius, 2)
    reduction_error = round(abs(predicted.risk_reduction - reduction_observed), 2)
    baseline_error = round(abs(predicted.before.blast_radius - observed_before.report.blast_radius), 2)

    comparison: dict[str, Any] = {
        "serviceSurvival": {"services": services, "agreement": f"{sum(s['match'] for s in services)}/{len(services)}",
                            "falsePositives": list(sets.service_false_positives),
                            "falseNegatives": list(sets.service_false_negatives)},
        "mandatoryWorkflowSurvival": {"workflowId": predicted.workflow_id, "predictedSurvives": workflow_predicted,
                                      "observedSurvives": workflow_observed, "match": workflow_predicted == workflow_observed,
                                      "mandatoryServices": list(workflow.mandatory_services)},
        "blastRadius": {"predictedBefore": predicted.before.blast_radius, "observedBefore": observed_before.report.blast_radius,
                        "baselineError": baseline_error, "baselineBasis": observed_before.basis,
                        "predictedAfter": after_p.blast_radius, "observedAfter": after_o.blast_radius, "absoluteError": error},
        "riskReduction": {"predicted": predicted.risk_reduction, "observed": reduction_observed,
                          "absoluteError": reduction_error, "observedBasis": observed_before.basis},
        "predictionError": {"primary": error, "meaning": "absolute difference, in percentage points, between the "
                            "predicted and the observed blast radius after the change"},
    }
    if placed is not None:
        observed = observed_placement(placed, list(predicted.placement))
        comparison["placement"] = {"predicted": predicted.placement, "observed": observed,
                                   "match": observed == predicted.placement,
                                   "note": "Pods are replaced by a rollout, so placement is compared as ready replicas per node."}

    reasons: list[str] = []
    if thresholds.require_workflow_match and workflow_predicted != workflow_observed:
        reasons.append(f"Workflow {predicted.workflow_id} was predicted to {'survive' if workflow_predicted else 'fail'} "
                       f"and was observed to {'survive' if workflow_observed else 'fail'}.")
    if thresholds.require_service_match and not sets.services_match:
        reasons.append("Failed services differ: predicted-only " + (", ".join(sets.service_false_positives) or "none")
                       + "; observed-only " + (", ".join(sets.service_false_negatives) or "none") + ".")
    if error > thresholds.blast_radius_tolerance:
        reasons.append(f"Blast radius after the change differs by {error} points (tolerance "
                       f"{thresholds.blast_radius_tolerance}).")
    if reduction_error > thresholds.risk_reduction_tolerance:
        reasons.append(f"Risk reduction differs by {reduction_error} points (tolerance "
                       f"{thresholds.risk_reduction_tolerance}).")
    if observed_before.basis == DERIVED:
        comparison["riskReduction"]["caveat"] = ("The baseline was derived from the observed placement; no failure was "
                                                 "injected before the change, so the observed risk reduction is partly a prediction.")
    return comparison, not reasons, tuple(reasons), error
