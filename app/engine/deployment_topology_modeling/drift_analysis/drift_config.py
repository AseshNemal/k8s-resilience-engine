from dataclasses import dataclass
from app.engine.deployment_topology_modeling.drift_analysis.constants import FailureDomainType


@dataclass(frozen=True)
class DriftConfig:
    """Settings that change drift-analysis behaviour. Every field here is read by a detector."""

    # Topology key used when a spread constraint omits topologyKey (Kubernetes requires it,
    # so this only applies to malformed manifests).
    failure_domain_key: str = FailureDomainType.HOSTNAME.value

    # Emit informational RESOURCE_CONTENTION_DRIFT findings for containers without limits.
    enforce_resource_limits: bool = True

    enable_audit_logging: bool = True
