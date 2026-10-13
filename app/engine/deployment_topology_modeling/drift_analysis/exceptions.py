class DriftAnalysisError(Exception):
    """Base exception for all drift analysis and placement exposure evaluation failures."""
    def __init__(self, message: str, service_name: str | None = None):
        super().__init__(message)
        self.service_name = service_name


class ExposureEvaluationError(DriftAnalysisError):
    """Raised when single-node failure domain simulation encounters inconsistent cluster state."""
    pass


class InvalidTopologyConstraintError(DriftAnalysisError):
    """Raised when declared affinity or topologySpreadConstraint syntax is unparseable."""
    pass


class DriftThresholdExceededError(DriftAnalysisError):
    """Raised when measured drift or concentration exceeds allowable research boundaries."""
    pass
