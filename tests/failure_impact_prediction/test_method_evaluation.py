import pytest

from app.engine.failure_impact_prediction.baseline_comparison import (
    compare_failure_impact_methods,
)
from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.method_evaluation import (
    IncompleteMethodEvaluationEvidenceError,
    evaluate_failure_impact_methods,
)
from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
)
from app.engine.failure_impact_prediction.validation_metrics import (
    ClassificationCounts,
    calculate_classification_metrics,
)


def _comparison():
    workflow, deployment = get_demo_inputs()
    return compare_failure_impact_methods(
        workflow,
        deployment,
        "node-1",
        dependency_scenario_services={"cartservice"},
    )


def _observation(*, failed_node: str = "node-1") -> ObservedFailureOutcome:
    return ObservedFailureOutcome(
        experiment_id="node-1-controlled-failure",
        failed_node=failed_node,
        failed_services=("cartservice",),
        failed_workflows=("checkout",),
    )


def _evaluate():
    return evaluate_failure_impact_methods(
        _comparison(),
        _observation(),
        observed_service_scope=(
            "frontend",
            "paymentservice",
            "cartservice",
            "checkoutservice",
            "emailservice",
        ),
        observed_workflow_scope=("checkout",),
    )


def test_evaluator_scores_only_supported_method_dimensions() -> None:
    evaluation = _evaluate()

    assert evaluation.replica_count_only.service_evaluation is not None
    assert evaluation.replica_count_only.workflow_evaluation is None
    assert evaluation.deployment_only.service_evaluation is not None
    assert evaluation.deployment_only.workflow_evaluation is None
    assert evaluation.dependency_only.service_evaluation is None
    assert evaluation.dependency_only.workflow_evaluation is not None
    assert evaluation.proposed_cross_layer.service_evaluation is not None
    assert evaluation.proposed_cross_layer.workflow_evaluation is not None


def test_evaluator_distinguishes_baseline_and_cross_layer_accuracy() -> None:
    evaluation = _evaluate()
    replica = evaluation.replica_count_only.service_evaluation
    deployment = evaluation.deployment_only.service_evaluation
    dependency = evaluation.dependency_only.workflow_evaluation
    proposed_service = evaluation.proposed_cross_layer.service_evaluation
    proposed_workflow = evaluation.proposed_cross_layer.workflow_evaluation

    assert replica is not None
    assert replica.true_positives == ()
    assert replica.false_positives == ("emailservice",)
    assert replica.false_negatives == ("cartservice",)
    assert replica.exact_match is False

    assert deployment is not None
    assert deployment.exact_match is True
    assert deployment.metrics.precision == pytest.approx(1.0)
    assert deployment.metrics.recall == pytest.approx(1.0)

    assert dependency is not None
    assert dependency.exact_match is True
    assert proposed_service is not None
    assert proposed_service.exact_match is True
    assert proposed_workflow is not None
    assert proposed_workflow.exact_match is True


def test_evaluator_refuses_unprobed_method_claims() -> None:
    with pytest.raises(
        IncompleteMethodEvaluationEvidenceError,
        match="emailservice",
    ):
        evaluate_failure_impact_methods(
            _comparison(),
            _observation(),
            observed_service_scope=("cartservice",),
            observed_workflow_scope=("checkout",),
        )


def test_evaluator_rejects_failed_node_mismatch() -> None:
    with pytest.raises(ValueError, match="same failed node"):
        evaluate_failure_impact_methods(
            _comparison(),
            _observation(failed_node="node-2"),
            observed_service_scope=("cartservice", "emailservice"),
            observed_workflow_scope=("checkout",),
        )


def test_evaluator_rejects_duplicate_observation_scope() -> None:
    with pytest.raises(ValueError, match="contains duplicates"):
        evaluate_failure_impact_methods(
            _comparison(),
            _observation(),
            observed_service_scope=("cartservice", "cartservice"),
            observed_workflow_scope=("checkout",),
        )


def test_public_metric_calculator_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        calculate_classification_metrics(
            ClassificationCounts(
                true_positives=-1,
                false_positives=0,
                false_negatives=0,
            )
        )
