"""
Domain Exceptions for Phase 4: Controlled Chaos & Disruption Drift Experiments.
Component: Component 2 - Deployment Topology Modeling
"""


class PlacementDriftExperimentError(Exception):
    """Base exception for all Phase 4 placement drift experiment errors."""
    pass


class ChaosSafetyError(PlacementDriftExperimentError):
    """Raised when an operation violates safety guardrails (e.g. protected namespace)."""
    pass


class ScenarioExecutionError(PlacementDriftExperimentError):
    """Raised when a disruption fails during preparation, execution, or observation."""
    pass


class StateRollbackError(PlacementDriftExperimentError):
    """Raised when an experiment fails to restore the cluster to its baseline state."""
    pass


class SnapshotPersistenceError(PlacementDriftExperimentError):
    """Raised when saving, indexing, or reloading cluster snapshots fails."""
    pass
