from typing import List, Dict, Any
from app.engine.deployment_topology_modeling.manifest_parsing.manifest_models import DeclaredIntent, ResourceLimits, TopologyConstraint

def parse_declared_intents(manifests: List[Dict[str, Any]]) -> List[DeclaredIntent]:
    """
    Extracts strictly typed Original Constraints from the raw parsed YAML dictionaries.
    Filters exclusively for 'Deployment' kinds and skips Services/Ingresses.
    """
    intents = []

    for item in manifests:
        # Ignore unsupported object kinds silently
        kind = item.get("kind", "")
        if kind != "Deployment":
            continue

        metadata = item.get("metadata", {})
        service_name = metadata.get("name", "unknown")

        spec = item.get("spec", {})
        expected_replicas = spec.get("replicas", 1)

        template_spec = spec.get("template", {}).get("spec", {})
        
        # Extract CPU/Memory limits safely bypassing nested IndexErrors
        containers = template_spec.get("containers", [])
        res_limits = ResourceLimits()
        
        if len(containers) > 0:
            first_container_limits = containers[0].get("resources", {}).get("limits", {})
            cpu_lim = first_container_limits.get("cpu")
            mem_lim = first_container_limits.get("memory")
            
            if cpu_lim:
                res_limits.cpu = str(cpu_lim)
            if mem_lim:
                res_limits.memory = str(mem_lim)

        # Extract Topology Spread Constraints safely
        spread_constraints_raw = template_spec.get("topologySpreadConstraints", [])
        parsed_spread_constraints = []
        for constraint in spread_constraints_raw:
            parsed_spread_constraints.append(TopologyConstraint(
                max_skew=constraint.get("maxSkew", 1),
                topology_key=constraint.get("topologyKey", "kubernetes.io/hostname"),
                when_unsatisfiable=constraint.get("whenUnsatisfiable", "DoNotSchedule")
            ))

        # Identify anti-affinity deployment intent for scoring
        affinity = template_spec.get("affinity", {})
        has_node_affinity = "nodeAffinity" in affinity or "podAntiAffinity" in affinity

        intents.append(DeclaredIntent(
            service_name=service_name,
            expected_replicas=expected_replicas,
            resource_limits=res_limits,
            spread_constraints=parsed_spread_constraints,
            has_node_affinity=has_node_affinity,
            # Defaults to True, true logic is fully realized in Phase 3 Drift check
            is_topology_compliant=True 
        ))

    return intents
