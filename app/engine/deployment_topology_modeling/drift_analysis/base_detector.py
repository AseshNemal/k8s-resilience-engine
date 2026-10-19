from abc import ABC, abstractmethod
from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.drift_config import DriftConfig
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import DriftViolation


class BaseDriftDetector(ABC):
    """
    Abstract Base Class for resilience drift detectors.
    
    Adheres to the Strategy Pattern, allowing independent evaluation of
    topology spread, anti-affinity, replica scaling, and resource contention.
    """
    def __init__(self, config: DriftConfig | None = None):
        self.config = config or DriftConfig()

    @abstractmethod
    def detect(
        self,
        service_name: str,
        declared_intent: Any | None,
        runtime_pods: list[dict[str, Any]],
        cluster_nodes: list[dict[str, Any]],
    ) -> list[DriftViolation]:
        """
        Detects drift violations for a specific microservice.
        
        Args:
            service_name: Unique service identifier.
            declared_intent: Parsed manifest or intent dictionary (from Phase 2).
            runtime_pods: List of runtime pod dictionaries (from Phase 1).
            cluster_nodes: List of cluster node dictionaries (from Phase 1).
            
        Returns:
            List of detected DriftViolation objects.
        """
        pass

    def is_compliant(self, violations: list[DriftViolation]) -> bool:
        """Determines if the service satisfies this detector's constraints."""
        return len(violations) == 0
