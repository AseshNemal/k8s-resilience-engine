from dataclasses import replace

import pytest

from app.engine.failure_impact_prediction.baseline_comparison import (
    compare_failure_impact_methods,
)
from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.method_evaluation import (
    evaluate_failure_impact_methods,
)
from app.engine.failure_impact_prediction.method_evaluation_batch import (
    build_method_evaluation_batch,
)
from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
)


SERVICE_SCOPE = (
    "frontend",
    "paymentservice",
    "cartservice",
    "checkoutservice",
    "emailservice",
)
WORKFLOW_SCOPE = ("checkout",)


def _evaluation(
    *,
    experiment_id: str,
    failed_node: str,
    failed_services: tuple[str, ...],
    failed_workflows: tuple[str, ...],
):
    workflow, deployment = get_demo_inputs()
    comparison = compare_failure_impact_methods(
        workflow,
        deployment,
        failed_node,
        dependency_scenario_services={"cartservice"},
    )
    return evaluate_failure_impact_methods(
        comparison,
        ObservedFailureOutcome(
            experiment_id=experiment_id,
            failed_node=failed_node,
            failed_services=failed_services,
            failed_workflows=failed_workflows,
        ),
        observed_service_scope=SERVICE_SCOPE,
        observed_workflow_scope=WORKFLOW_SCOPE,
    )


def _evaluations():
    return (
        _evaluation(
            experiment_id="node-1-run",
            failed_node="node-1",
            failed_services=("cartservice",),
            failed_workflows=("checkout",),
        ),
        _evaluation(
            experiment_id="node-2-run",
            failed_node="node-2",
            failed_services=(),
            failed_workflows=(),
        ),
    )


def test_batch_micro_averages_each_applicable_method_dimension() -> None:
    batch = build_method_evaluation_batch(
        "worker-node-method-comparison",
        _evaluations(),
    )

    assert batch.total_experiments == 2
    assert batch.experiment_ids == ("node-1-run", "node-2-run")

    replica = batch.replica_count_only.service_metrics
    assert replica is not None
    assert replica.classification.counts.true_positives == 0
    assert replica.classification.counts.false_positives == 2
    assert replica.classification.counts.false_negatives == 1
    assert replica.exact_match_count == 0
    assert replica.exact_match_rate == pytest.approx(0.0)

    deployment = batch.deployment_only.service_metrics
    assert deployment is not None
    assert deployment.classification.counts.true_positives == 1
    assert deployment.classification.counts.false_positives == 0
    assert deployment.classification.counts.false_negatives == 0
    assert deployment.exact_match_rate == pytest.approx(1.0)

    dependency = batch.dependency_only.workflow_metrics
    assert dependency is not None
    assert dependency.classification.precision == pytest.approx(0.5)
    assert dependency.classification.recall == pytest.approx(1.0)
    assert dependency.classification.f1_score == pytest.approx(2 / 3)
    assert dependency.exact_match_rate == pytest.approx(0.5)

    proposed_service = batch.proposed_cross_layer.service_metrics
    proposed_workflow = batch.proposed_cross_layer.workflow_metrics
    assert proposed_service is not None
    assert proposed_workflow is not None
    assert proposed_service.exact_match_rate == pytest.approx(1.0)
    assert proposed_workflow.exact_match_rate == pytest.approx(1.0)


def test_batch_preserves_not_applicable_dimensions() -> None:
    batch = build_method_evaluation_batch("method-batch", _evaluations())

    assert batch.replica_count_only.workflow_metrics is None
    assert batch.deployment_only.workflow_metrics is None
    assert batch.dependency_only.service_metrics is None


def test_batch_rejects_duplicate_experiment_ids() -> None:
    first, second = _evaluations()
    duplicate = replace(second, experiment_id=first.experiment_id)

    with pytest.raises(ValueError, match="Duplicate experiment IDs"):
        build_method_evaluation_batch("method-batch", (first, duplicate))


def test_batch_rejects_inconsistent_dimension_applicability() -> None:
    first, second = _evaluations()
    altered_replica = replace(
        second.replica_count_only,
        service_evaluation=None,
    )
    altered = replace(second, replica_count_only=altered_replica)

    with pytest.raises(ValueError, match="applicability must be consistent"):
        build_method_evaluation_batch("method-batch", (first, altered))


def test_batch_requires_nonempty_identity_and_evaluations() -> None:
    with pytest.raises(ValueError, match="batch_id"):
        build_method_evaluation_batch(" ", _evaluations())
    with pytest.raises(ValueError, match="At least one"):
        build_method_evaluation_batch("method-batch", ())
