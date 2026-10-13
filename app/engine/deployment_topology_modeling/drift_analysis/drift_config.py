from dataclasses import dataclass
from app.engine.deployment_topology_modeling.drift_analysis.constants import FailureDomainType


@dataclass(frozen=True)
class DriftConfig:
    """
    Configurable parameters and threshold settings for resilience drift analysis.
    
    Prevents hardcoding magic numbers and allows calibration across different 
    cluster sizes and experiment benchmarks (e.g. TeaStore vs. Boutique).
    """
    # Concentration thresholds
    concentration_threshold: float = 0.50          # Flag if >= 50% replicas share one node
    critical_concentration_threshold: float = 1.00 # Flag critical if 100% replicas on one node
    
    # Topology spread constraints
    default_max_skew: int = 1                      # Max allowable difference between failure domains
    failure_domain_key: str = FailureDomainType.HOSTNAME.value
    
    # Replica scaling thresholds
    allow_scale_divergence: bool = False           # Flag if ready_replicas != declared_replicas
    
    # Resource contention thresholds
    enforce_resource_limits: bool = True           # Flag containers without CPU/memory limits
    min_recommended_cpu_cores: float = 0.1
    min_recommended_memory_mb: float = 64.0
    
    # Exposure classification parameters
    treat_single_replica_as_exposed: bool = True   # Services with N=1 replica are inherently exposed
    
    # Audit trail
    enable_audit_logging: bool = True
