"""
Shared simulation engine tests.

Covers:
  A. Original graph is immutable after simulation.
  B. Before snapshot is isolated from after snapshot.
  C. Two simulations from the same original share no mutable state.
  D. Invalid mutation output (non-DeploymentGraph) raises TypeError.
  E. A component-owned mutation (Member 4 adapter) works end-to-end.
"""

import pytest

from app.engine.simulation import simulate_deployment_change
from app.engine.recommendation_evaluation_validation.mutations import (
    TopologySpreadMutation,
)
from app.shared.sample_loader import load_sample_json
from app.shared.schemas.deployment import DeploymentGraph


# ---------------------------------------------------------------------------
# Helpers / test fixtures
# ---------------------------------------------------------------------------


class MoveFirstPaymentPod:
    """Moves the first paymentservice pod to a different node."""

    def apply(
        self,
        deployment: DeploymentGraph,
    ) -> DeploymentGraph:
        service = next(
            item
            for item in deployment.services
            if item.name == "paymentservice"
        )
        service.pods[0].node = "node-3"
        service.pods[0].zone = "zone-b"
        return deployment


class InvalidMutation:
    """Returns None instead of a DeploymentGraph — must be rejected."""

    def apply(self, deployment: DeploymentGraph):
        return None


class MutateAfterReturn:
    """Returns the deployment then mutates it externally — tests before isolation."""

    def __init__(self) -> None:
        self._returned: DeploymentGraph | None = None

    def apply(self, deployment: DeploymentGraph) -> DeploymentGraph:
        self._returned = deployment
        return deployment

    @property
    def returned(self) -> DeploymentGraph | None:
        return self._returned


def _deployment() -> DeploymentGraph:
    return DeploymentGraph.model_validate(
        load_sample_json("deployment.json")
    )


# ---------------------------------------------------------------------------
# A — Original graph is immutable (pre-existing test, kept intact)
# ---------------------------------------------------------------------------


def test_shared_simulation_does_not_mutate_input() -> None:
    """Task A: the caller's original DeploymentGraph is unchanged."""
    deployment = _deployment()

    result = simulate_deployment_change(
        deployment,
        MoveFirstPaymentPod(),
    )

    assert deployment.services[0].pods[0].node == "node-1"
    assert result.before.services[0].pods[0].node == "node-1"
    assert result.after.services[0].pods[0].node == "node-3"


# ---------------------------------------------------------------------------
# B — before snapshot is isolated from after snapshot
# ---------------------------------------------------------------------------


def test_before_snapshot_isolated_from_after() -> None:
    """Task B: mutating `after` must not retroactively change `before`."""
    deployment = _deployment()

    result = simulate_deployment_change(
        deployment,
        MoveFirstPaymentPod(),
    )

    # before recorded node-1; after recorded node-3
    before_node = result.before.services[0].pods[0].node
    after_node = result.after.services[0].pods[0].node

    assert before_node == "node-1"
    assert after_node == "node-3"

    # Now mutate after in-place and confirm before is unaffected
    result.after.services[0].pods[0].node = "node-99"
    assert result.before.services[0].pods[0].node == "node-1"


# ---------------------------------------------------------------------------
# C — Two independent simulation runs share no mutable state
# ---------------------------------------------------------------------------


def test_two_simulation_runs_are_isolated() -> None:
    """Task C: two simulations from the same original must not share state."""
    deployment = _deployment()

    result_a = simulate_deployment_change(deployment, MoveFirstPaymentPod())
    result_b = simulate_deployment_change(deployment, MoveFirstPaymentPod())

    # Independently mutate result_a's after
    result_a.after.services[0].pods[0].node = "node-99"

    # result_b's after must be unaffected
    assert result_b.after.services[0].pods[0].node == "node-3"

    # original must also be unaffected
    assert deployment.services[0].pods[0].node == "node-1"


# ---------------------------------------------------------------------------
# D — Invalid mutation output raises TypeError (pre-existing, kept intact)
# ---------------------------------------------------------------------------


def test_shared_simulation_rejects_invalid_mutation_result() -> None:
    """Task D: mutation returning non-DeploymentGraph raises TypeError."""
    deployment = _deployment()

    try:
        simulate_deployment_change(deployment, InvalidMutation())
    except TypeError as error:
        assert "DeploymentGraph" in str(error)
    else:
        raise AssertionError("Expected invalid mutation result to fail.")


# ---------------------------------------------------------------------------
# E — Member 4 adapter (TopologySpreadMutation) works end-to-end
# ---------------------------------------------------------------------------


def test_topology_spread_mutation_via_shared_engine() -> None:
    """Task E: Member 4's TopologySpreadMutation works with the shared engine."""
    deployment = _deployment()

    # paymentservice has 3 pods on 2 nodes; topology-spread should redistribute.
    result = simulate_deployment_change(
        deployment,
        TopologySpreadMutation("paymentservice"),
    )

    # Original is untouched.
    original_nodes = [pod.node for pod in deployment.services[0].pods]
    assert original_nodes == ["node-1", "node-1", "node-2"]

    # before snapshot matches original.
    before_nodes = [pod.node for pod in result.before.services[0].pods]
    assert before_nodes == original_nodes

    # after snapshot reflects redistribution across available nodes.
    after_nodes = [pod.node for pod in result.after.services[0].pods]
    # All three pods should be spread; at minimum they should differ from the
    # concentrated original layout (all concentrated on node-1).
    assert after_nodes != before_nodes


def test_topology_spread_mutation_reduces_concentration_via_shared_engine() -> None:
    """Task E (supplementary): concentration actually decreases."""
    from app.engine.recommendation_evaluation_validation.metrics import (
        calculate_replica_concentration,
    )

    deployment = _deployment()

    result = simulate_deployment_change(
        deployment,
        TopologySpreadMutation("paymentservice"),
    )

    concentration_before = calculate_replica_concentration(
        result.before, "paymentservice"
    )
    concentration_after = calculate_replica_concentration(
        result.after, "paymentservice"
    )

    assert concentration_after == pytest.approx(0.33, abs=0.01)
    assert concentration_after < concentration_before
