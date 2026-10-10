from typing import Dict, Any
from app.engine.deployment_topology_modeling.manifest_parsing.constants import DEFAULT_CPU_LIMIT, DEFAULT_MEMORY_LIMIT

def extract_cpu_limit(limits_dict: Dict[str, Any]) -> str:
    """Isolates CPU string bounds securely."""
    cpu_lim = limits_dict.get("cpu")
    if cpu_lim:
        return str(cpu_lim)
    return DEFAULT_CPU_LIMIT

def extract_memory_limit(limits_dict: Dict[str, Any]) -> str:
    """Isolates Memory string bounds securely."""
    mem_lim = limits_dict.get("memory")
    if mem_lim:
        return str(mem_lim)
    return DEFAULT_MEMORY_LIMIT
