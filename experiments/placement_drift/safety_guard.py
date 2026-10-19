"""
Safety Guard and Pre-flight Verification for Phase 4 Chaos Experiments.
Component: Component 2 - Deployment Topology Modeling
"""
import logging
from typing import Any, Dict, List

from experiments.placement_drift.constants import (
    PROTECTED_NAMESPACES,
    DEFAULT_CHAOS_NAMESPACE,
    ScenarioExecutionMode,
)
from experiments.placement_drift.exceptions import ChaosSafetyError

logger = logging.getLogger(__name__)


class SafetyGuard:
    """
    Enforces strict pre-flight and runtime guardrails to prevent chaos disruptions
    from affecting system or production namespaces.
    """

    def __init__(
        self,
        allowed_namespace: str = DEFAULT_CHAOS_NAMESPACE,
        execution_mode: ScenarioExecutionMode = ScenarioExecutionMode.DRY_RUN_SIMULATION,
        allow_default_namespace: bool = True,
    ):
        self.allowed_namespace = allowed_namespace
        self.execution_mode = execution_mode
        self.allow_default_namespace = allow_default_namespace

    def validate_namespace_safety(self, target_namespace: str) -> None:
        """Verifies that the target namespace is safe for controlled disruptions."""
        if not target_namespace or not target_namespace.strip():
            raise ChaosSafetyError("Target namespace cannot be empty or null.")

        clean_ns = target_namespace.strip()

        # Critical: Block core system infrastructure namespaces
        if clean_ns in PROTECTED_NAMESPACES:
            if clean_ns == "default" and self.allow_default_namespace:
                logger.warning("Operating in 'default' namespace under controlled permissions.")
                return
            raise ChaosSafetyError(
                f"Safety Guard Abort: Namespace '{clean_ns}' is protected. "
                f"Chaos disruptions are strictly prohibited against system namespaces."
            )

    def validate_preflight_nodes(self, nodes: List[Dict[str, Any]]) -> None:
        """Ensures the cluster has sufficient worker nodes to observe realistic scheduling."""
        if not nodes:
            raise ChaosSafetyError(
                "Safety Guard Abort: No worker nodes available for experiment execution."
            )
        if len(nodes) < 2 and self.execution_mode == ScenarioExecutionMode.LIVE_CLUSTER:
            logger.warning(
                "Single worker node detected. Pod anti-affinity cannot distribute replicas."
            )

    def validate_workload_presence(self, pods: List[Any], service_name: str) -> None:
        """Verifies target microservice pods exist before attempting disruption."""
        matching_pods = [
            p for p in pods
            if getattr(p, "service_name", None) == service_name
            or (isinstance(p, dict) and p.get("service_name") == service_name)
        ]
        if not matching_pods:
            raise ChaosSafetyError(
                f"Safety Guard Abort: No active pods found for service '{service_name}'."
            )

    def can_execute_destructive_action(self, action_name: str) -> bool:
        """Checks if a destructive action is permitted under the current execution mode."""
        if self.execution_mode == ScenarioExecutionMode.DRY_RUN_SIMULATION:
            logger.info("Dry-run simulation mode active: live cluster modification blocked.")
            return False
        logger.warning("Executing LIVE destructive cluster action: '%s'", action_name)
        return True
