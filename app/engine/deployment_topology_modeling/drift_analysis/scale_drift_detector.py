from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftType,
    DriftSeverity,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import DriftViolation
from app.engine.deployment_topology_modeling.drift_analysis.base_detector import BaseDriftDetector


class ScaleDriftDetector(BaseDriftDetector):
    """
    Detects scale drift where the number of active, ready runtime pods diverges 
    from the declared manifest intent (e.g. due to crashloops, pod pending, or manual scaling).
    """

    def detect(
        self,
        service_name: str,
        declared_intent: Any | None,
        runtime_pods: list[dict[str, Any]],
        cluster_nodes: list[dict[str, Any]],
    ) -> list[DriftViolation]:
        violations: list[DriftViolation] = []

        if not declared_intent:
            return violations

        # Extract declared replica count (supports object attributes or dict keys)
        if hasattr(declared_intent, "expected_replicas"):
            declared_replicas = getattr(declared_intent, "expected_replicas", None)
        elif hasattr(declared_intent, "replicas"):
            declared_replicas = getattr(declared_intent, "replicas", None)
        elif isinstance(declared_intent, dict):
            declared_replicas = declared_intent.get("expected_replicas", declared_intent.get("replicas", None))
        else:
            declared_replicas = None

        if declared_replicas is None:
            return violations

        # Count ready pods
        ready_pods_count = sum(
            1 for pod in runtime_pods
            if pod.get("is_ready", pod.get("ready", False))
        )
        total_pods_count = len(runtime_pods)

        if ready_pods_count != declared_replicas:
            if ready_pods_count == 0:
                severity = DriftSeverity.CRITICAL
                desc = (
                    f"Service '{service_name}' has 0 ready pods out of {declared_replicas} "
                    f"declared replicas (total pods: {total_pods_count}). Service is completely down."
                )
            elif ready_pods_count < declared_replicas:
                severity = DriftSeverity.HIGH
                desc = (
                    f"Service '{service_name}' has {ready_pods_count} ready pods out of {declared_replicas} "
                    f"declared replicas. Operating at degraded capacity."
                )
            else:
                severity = DriftSeverity.LOW
                desc = (
                    f"Service '{service_name}' has {ready_pods_count} ready pods, exceeding {declared_replicas} "
                    f"declared replicas (likely autoscaling or manual rollout)."
                )

            violations.append(
                DriftViolation(
                    drift_type=DriftType.SCALE_DRIFT,
                    severity=severity,
                    description=desc,
                    expected=declared_replicas,
                    actual=ready_pods_count,
                )
            )

        return violations
