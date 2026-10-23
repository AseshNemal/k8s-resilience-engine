"""Shared wiring for tests that run the Kubernetes executor against the in-memory gateway (TEST-ONLY)."""

from app.engine.recommendation_evaluation_validation.validation import DisposableClusterDesignation
from app.engine.recommendation_evaluation_validation.validation.kubernetes_executor import KubernetesValidationExecutor
from tests.support.member4_approvals import approved_for_test
from tests.support.member4_fake_gateway import FakeClock, FakeClusterGateway, FakeNodeFault, spread_least_loaded
from tests.support.member4_harness import OFFLINE_NAMESPACE, bookinfo_scenario

UID = "6f1e2c1a-0000-4000-8000-0000000000aa"


def designation(**overrides):
    values = dict(name="kind-resilience-test", api_server="https://127.0.0.1:6443", cluster_uid=UID,
                  namespace=OFFLINE_NAMESPACE, designated_by="asesh")
    return DisposableClusterDesignation(**{**values, **overrides})


class Setup:
    def __init__(self, *, gateway_options=None, fault_options=None, executor_options=None, graph=None):
        self.scenario = bookinfo_scenario("concentrated")
        self.graph = graph or self.scenario.deployment
        self.gateway = FakeClusterGateway(self.graph, namespace=OFFLINE_NAMESPACE, scheduler=spread_least_loaded,
                                          **(gateway_options or {}))
        self.gateway.remember_original()
        self.fault = FakeNodeFault(self.gateway, **(fault_options or {}))
        self.clock = FakeClock()
        self.options = {"writes_enabled": True, "sleep": self.clock.sleep, "clock": self.clock, **(executor_options or {})}

    def executor(self, **overrides) -> KubernetesValidationExecutor:
        return KubernetesValidationExecutor(self.gateway, self.fault, designation(), **{**self.options, **overrides})

    def proposal(self):
        approval, live = approved_for_test(self.scenario)
        return approval, live, approval.yaml_snapshot
