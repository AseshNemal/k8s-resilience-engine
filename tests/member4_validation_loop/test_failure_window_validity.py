"""A failure only counts when the node stayed down for the whole window; a node that restarts itself is not a measurement."""

import pytest

from app.engine.recommendation_evaluation_validation.validation.models import ValidationInterrupted  # noqa: F401
from tests.support.member4_k8s_setup import Setup


def prepared():
    setup = Setup()
    approval, live, yaml_text = setup.proposal()
    executor = setup.executor()
    executor.apply(yaml_text)
    executor.await_settled()
    return setup, executor


def test_a_node_that_stays_down_through_the_window_is_a_valid_failure():
    setup, executor = prepared()

    graph = executor.fail_node("w1")
    executor.restore_node("w1")

    assert any(pod.node == "w1" and not pod.ready for svc in graph.services for pod in svc.pods)


def test_a_node_that_restarts_itself_during_the_window_is_refused_as_not_a_measurement_and_is_still_restored():
    setup, executor = prepared()
    asked = {"after_down": 0}
    original = setup.gateway.node_ready

    def restarts_itself(node):
        ready = original(node)
        if ready is False:
            asked["after_down"] += 1
            if asked["after_down"] >= 2:          # NotReady was observed once (detection) and was Ready again at the end of the window
                setup.gateway.recover_node(node)
                return True
        return ready

    setup.gateway.node_ready = restarts_itself

    with pytest.raises(RuntimeError, match="reported Ready again"):
        executor.fail_node("w1")
    executor.restore_node("w1")           # the loop restores in a finally; restoring a node that is already back is harmless
