import pytest

from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
    compare_prediction_with_observation,
)
from app.engine.failure_impact_prediction.service import predict_failure_impact


def _node_one_prediction():
    workflow, deployment = get_demo_inputs()
    return predict_failure_impact(workflow, deployment, "node-1")


def test_exact_observation_matches_prediction() -> None:
    prediction = _node_one_prediction()
    observation = ObservedFailureOutcome(
        experiment_id="experiment-node-1",
        failed_node="node-1",
        failed_services=("cartservice",),
        failed_workflows=("checkout",),
    )

    comparison = compare_prediction_with_observation(
        prediction,
        observation,
    )

    assert comparison.service_true_positives == ("cartservice",)
    assert comparison.workflow_true_positives == ("checkout",)
    assert comparison.service_false_positives == ()
    assert comparison.service_false_negatives == ()
    assert comparison.workflow_false_positives == ()
    assert comparison.workflow_false_negatives == ()
    assert comparison.services_match is True
    assert comparison.workflows_match is True
    assert comparison.exact_match is True


def test_comparison_explains_overpredicted_and_missed_failures() -> None:
    prediction = _node_one_prediction()
    observation = ObservedFailureOutcome(
        experiment_id="experiment-node-1-mismatch",
        failed_node="node-1",
        failed_services=("cartservice", "paymentservice"),
        failed_workflows=(),
    )

    comparison = compare_prediction_with_observation(
        prediction,
        observation,
    )

    assert comparison.service_true_positives == ("cartservice",)
    assert comparison.service_false_positives == ()
    assert comparison.service_false_negatives == ("paymentservice",)
    assert comparison.workflow_true_positives == ()
    assert comparison.workflow_false_positives == ("checkout",)
    assert comparison.workflow_false_negatives == ()
    assert comparison.services_match is False
    assert comparison.workflows_match is False
    assert comparison.exact_match is False


def test_comparison_rejects_different_failed_nodes() -> None:
    prediction = _node_one_prediction()
    observation = ObservedFailureOutcome(
        experiment_id="experiment-node-mismatch",
        failed_node="node-2",
        failed_services=(),
        failed_workflows=(),
    )

    with pytest.raises(ValueError, match="same failed node"):
        compare_prediction_with_observation(prediction, observation)


@pytest.mark.parametrize(
    ("field_name", "failed_services", "failed_workflows"),
    [
        ("observation.failed_services", ("cartservice", "cartservice"), ()),
        ("observation.failed_workflows", (), ("checkout", "checkout")),
    ],
)
def test_comparison_rejects_duplicate_observations(
    field_name: str,
    failed_services: tuple[str, ...],
    failed_workflows: tuple[str, ...],
) -> None:
    prediction = _node_one_prediction()
    observation = ObservedFailureOutcome(
        experiment_id="experiment-duplicate",
        failed_node="node-1",
        failed_services=failed_services,
        failed_workflows=failed_workflows,
    )

    with pytest.raises(ValueError, match=field_name):
        compare_prediction_with_observation(prediction, observation)


def test_comparison_rejects_blank_experiment_id() -> None:
    prediction = _node_one_prediction()
    observation = ObservedFailureOutcome(
        experiment_id="   ",
        failed_node="node-1",
        failed_services=("cartservice",),
        failed_workflows=("checkout",),
    )

    with pytest.raises(ValueError, match="experiment_id"):
        compare_prediction_with_observation(prediction, observation)


def test_comparison_does_not_modify_prediction_or_observation() -> None:
    prediction = _node_one_prediction()
    observation = ObservedFailureOutcome(
        experiment_id=" experiment-immutable ",
        failed_node=" node-1 ",
        failed_services=("cartservice",),
        failed_workflows=("checkout",),
    )
    prediction_before = prediction.model_dump()

    compare_prediction_with_observation(prediction, observation)

    assert prediction.model_dump() == prediction_before
    assert observation.experiment_id == " experiment-immutable "
    assert observation.failed_node == " node-1 "
