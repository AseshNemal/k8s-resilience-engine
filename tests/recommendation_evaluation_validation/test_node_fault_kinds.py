"""A graceful eviction is not a node failure, and the experiment says so instead of letting one pass for the other."""

import pytest

from app.engine.recommendation_evaluation_validation.validation import ValidationRefusedError
from app.engine.recommendation_evaluation_validation.validation.node_fault import (
    GENUINE_FAILURE_KINDS,
    NodeFaultKind,
    require_node_failure,
)


def injector(kind):
    class Injector:
        evidence_class = "TEST_DOUBLE"
        method = "m"

        def inject(self, node): ...
        def restore(self, node): ...
    instance = Injector()
    instance.kind = kind
    return instance


def test_only_a_stopped_node_counts_as_a_node_failure():
    assert GENUINE_FAILURE_KINDS == {NodeFaultKind.NODE_STOP}


def test_a_cordon_and_drain_is_refused_as_a_node_failure_with_the_reason_stated():
    with pytest.raises(ValidationRefusedError) as raised:
        require_node_failure(injector(NodeFaultKind.CORDON_DRAIN))

    assert raised.value.code == "FAULT_NOT_A_NODE_FAILURE"
    assert "graceful" in raised.value.message and "kubelet" in raised.value.message


def test_a_stopped_node_is_accepted():
    kind = injector(NodeFaultKind.NODE_STOP)

    assert require_node_failure(kind) is kind


def test_an_unknown_kind_is_refused_not_assumed_to_be_a_failure():
    with pytest.raises(ValidationRefusedError) as raised:
        require_node_failure(injector("PARTITION"))

    assert raised.value.code == "FAULT_NOT_A_NODE_FAILURE"
