"""What counts as a worker-node failure for a validation run, and what does not.

The prediction under test is "if this worker node fails, these services and workflows survive". Three different things can be
done to a node, and only one of them is that:

* ``NODE_STOP``: the node stops answering. Its kubelet and its network go away at once, with no warning to the control
  plane, exactly as in a crash or a power loss. The control plane notices only after its node-monitor grace period, marks the
  node NotReady, and marks the pods on it not Ready. This is the failure the model predicts. For a kind cluster it is stopping
  the node's container; for a VM it is powering it off.
* ``CORDON_DRAIN``: the node is marked unschedulable and its pods are evicted one by one while the node and its kubelet stay
  healthy. Pods get a termination grace period, their replacements are scheduled before or while they go, and
  PodDisruptionBudgets are honoured. It is a planned maintenance operation. It tells you how the cluster behaves when a node is
  taken out gently, not when a node is lost, so its outcome must never be reported as the outcome of a node failure.

An injector declares which of these it performs. The executor refuses any injector that is not a genuine node failure, at
construction and again before it fails a node, and records the method in the experiment record.
"""

from enum import Enum
from typing import Protocol

from app.engine.recommendation_evaluation_validation.validation.models import ValidationRefusedError


class NodeFaultKind(str, Enum):
    NODE_STOP = "NODE_STOP"
    CORDON_DRAIN = "CORDON_DRAIN"


GENUINE_FAILURE_KINDS = frozenset({NodeFaultKind.NODE_STOP})


class NodeFaultInjector(Protocol):
    """Stops and restarts a node by whatever the platform offers (a container, a VM). It never touches workloads."""

    kind: NodeFaultKind
    evidence_class: str        # REAL_CLUSTER for an injector that acts on a real node, TEST_DOUBLE otherwise
    method: str                # a human-readable account of what it does, kept in the experiment record

    def inject(self, node: str) -> None:
        """Stop the node. Returns when the stop was issued, not when the cluster noticed."""

    def restore(self, node: str) -> None:
        """Start the node again. Must be safe to call when the node was never stopped."""


def require_node_failure(injector: NodeFaultInjector) -> NodeFaultInjector:
    """``injector`` unchanged if it produces a genuine node failure; otherwise a refusal that says what it produces."""
    kind = getattr(injector, "kind", None)
    if kind not in GENUINE_FAILURE_KINDS:
        raise ValidationRefusedError(
            "FAULT_NOT_A_NODE_FAILURE",
            f"{getattr(kind, 'value', kind)!r} is not a node failure. A cordon and drain is a graceful eviction: the "
            f"node and its kubelet stay healthy and pods are terminated with a grace period, so it does not test what "
            f"the model predicts. A failure experiment needs the node itself to stop answering.")
    return injector
