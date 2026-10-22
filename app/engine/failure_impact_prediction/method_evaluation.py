"""Evaluate each failure-impact method against one observation scope."""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from app.engine.failure_impact_prediction.baseline_comparison import (
    FailureImpactMethodComparison,
)
from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
)
from app.engine.failure_impact_prediction.validation_metrics import (
    ClassificationCounts,
    ClassificationMetrics,
    calculate_classification_metrics,
)


MethodName = Literal[
    "REPLICA_COUNT_ONLY",
    "DEPLOYMENT_ONLY",
    "DEPENDENCY_ONLY",
    "PROPOSED_CROSS_LAYER",
]


class IncompleteMethodEvaluationEvidenceError(ValueError):
    """Raised when a method claim falls outside the observed probe scope."""


@dataclass(frozen=True)
class FailureSetEvaluation:
    """One method's claims evaluated within an explicit observation scope."""

    observation_scope: tuple[str, ...]
    predicted_failures: tuple[str, ...]
    observed_failures: tuple[str, ...]
    true_positives: tuple[str, ...]
    false_positives: tuple[str, ...]
    false_negatives: tuple[str, ...]
    metrics: ClassificationMetrics
    exact_match: bool


@dataclass(frozen=True)
class MethodEvidenceEvaluation:
    """Applicable service and workflow evaluations for one method."""

    method: MethodName
    service_evaluation: FailureSetEvaluation | None
    workflow_evaluation: FailureSetEvaluation | None
    interpretation: str


@dataclass(frozen=True)
class FailureImpactMethodEvaluation:
    """Evidence-backed evaluation of all methods for one experiment."""

    experiment_id: str
    failed_node: str
    replica_count_only: MethodEvidenceEvaluation
    deployment_only: MethodEvidenceEvaluation
    dependency_only: MethodEvidenceEvaluation
    proposed_cross_layer: MethodEvidenceEvaluation


def _identifiers(
    values: Collection[str],
    field_name: str,
) -> tuple[str, ...]:
    normalized = tuple(value.strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError(f"{field_name} must contain non-empty strings")
    if len(set(normalized)) != len(normalized):
        duplicates = sorted(
            {value for value in normalized if normalized.count(value) > 1}
        )
        raise ValueError(
            f"{field_name} contains duplicates: {', '.join(duplicates)}"
        )
    return tuple(sorted(normalized))


def _evaluate_failure_set(
    *,
    predicted: Collection[str],
    observed: Collection[str],
    observation_scope: tuple[str, ...],
    claim_name: str,
) -> FailureSetEvaluation:
    predicted_set = set(_identifiers(predicted, f"{claim_name}.predicted"))
    observed_set = set(_identifiers(observed, f"{claim_name}.observed"))
    scope_set = set(observation_scope)

    unobserved_claims = tuple(sorted(predicted_set - scope_set))
    if unobserved_claims:
        raise IncompleteMethodEvaluationEvidenceError(
            f"{claim_name} claims were not probed: "
            + ", ".join(unobserved_claims)
        )
    observed_outside_scope = tuple(sorted(observed_set - scope_set))
    if observed_outside_scope:
        raise ValueError(
            f"{claim_name} observations fall outside the declared scope: "
            + ", ".join(observed_outside_scope)
        )

    true_positives = tuple(sorted(predicted_set & observed_set))
    false_positives = tuple(sorted(predicted_set - observed_set))
    false_negatives = tuple(sorted(observed_set - predicted_set))
    metrics = calculate_classification_metrics(
        ClassificationCounts(
            true_positives=len(true_positives),
            false_positives=len(false_positives),
            false_negatives=len(false_negatives),
        )
    )
    return FailureSetEvaluation(
        observation_scope=observation_scope,
        predicted_failures=tuple(sorted(predicted_set)),
        observed_failures=tuple(sorted(observed_set)),
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        metrics=metrics,
        exact_match=predicted_set == observed_set,
    )


def evaluate_failure_impact_methods(
    comparison: FailureImpactMethodComparison,
    observation: ObservedFailureOutcome,
    *,
    observed_service_scope: Collection[str],
    observed_workflow_scope: Collection[str],
) -> FailureImpactMethodEvaluation:
    """Score only dimensions that each method is designed to predict."""

    experiment_id = observation.experiment_id.strip()
    if not experiment_id:
        raise ValueError("experiment_id must be a non-empty string")
    failed_node = observation.failed_node.strip()
    if not failed_node:
        raise ValueError("failed_node must be a non-empty string")
    if failed_node != comparison.failed_node:
        raise ValueError(
            "Method comparison and observation must use the same failed node: "
            f"{comparison.failed_node} != {failed_node}"
        )

    service_scope = _identifiers(
        observed_service_scope,
        "observed_service_scope",
    )
    workflow_scope = _identifiers(
        observed_workflow_scope,
        "observed_workflow_scope",
    )
    if not service_scope:
        raise ValueError("observed_service_scope must not be empty")
    if not workflow_scope:
        raise ValueError("observed_workflow_scope must not be empty")

    service_observations = observation.failed_services
    workflow_observations = observation.failed_workflows
    dependency_prediction = (
        ()
        if comparison.dependency_only.survives
        else (comparison.workflow_id,)
    )

    return FailureImpactMethodEvaluation(
        experiment_id=experiment_id,
        failed_node=failed_node,
        replica_count_only=MethodEvidenceEvaluation(
            method="REPLICA_COUNT_ONLY",
            service_evaluation=_evaluate_failure_set(
                predicted=comparison.replica_count_only.vulnerable_services,
                observed=service_observations,
                observation_scope=service_scope,
                claim_name="replica_count_only.services",
            ),
            workflow_evaluation=None,
            interpretation=(
                "Static replica vulnerability used as a service-failure proxy; "
                "workflow evaluation is not applicable."
            ),
        ),
        deployment_only=MethodEvidenceEvaluation(
            method="DEPLOYMENT_ONLY",
            service_evaluation=_evaluate_failure_set(
                predicted=comparison.deployment_only.unavailable_services,
                observed=service_observations,
                observation_scope=service_scope,
                claim_name="deployment_only.services",
            ),
            workflow_evaluation=None,
            interpretation=(
                "Node-specific service failure prediction; workflow evaluation "
                "is not applicable without dependency evidence."
            ),
        ),
        dependency_only=MethodEvidenceEvaluation(
            method="DEPENDENCY_ONLY",
            service_evaluation=None,
            workflow_evaluation=_evaluate_failure_set(
                predicted=dependency_prediction,
                observed=workflow_observations,
                observation_scope=workflow_scope,
                claim_name="dependency_only.workflows",
            ),
            interpretation=(
                "Workflow prediction for the predeclared service scenario; "
                "service evaluation is not applicable because service failures "
                "are inputs to this method."
            ),
        ),
        proposed_cross_layer=MethodEvidenceEvaluation(
            method="PROPOSED_CROSS_LAYER",
            service_evaluation=_evaluate_failure_set(
                predicted=comparison.proposed_cross_layer.failed_services,
                observed=service_observations,
                observation_scope=service_scope,
                claim_name="proposed_cross_layer.services",
            ),
            workflow_evaluation=_evaluate_failure_set(
                predicted=comparison.proposed_cross_layer.failed_workflows,
                observed=workflow_observations,
                observation_scope=workflow_scope,
                claim_name="proposed_cross_layer.workflows",
            ),
            interpretation=(
                "Node-specific service and workflow prediction from joined "
                "workflow and deployment evidence."
            ),
        ),
    )


__all__ = [
    "FailureImpactMethodEvaluation",
    "FailureSetEvaluation",
    "IncompleteMethodEvaluationEvidenceError",
    "MethodEvidenceEvaluation",
    "MethodName",
    "evaluate_failure_impact_methods",
]
