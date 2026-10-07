import json
import time
from dataclasses import asdict
from pathlib import Path
from app.shared.schemas.deployment import DeploymentGraph

# Core Domain Dependencies
from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import get_cluster_nodes, get_cluster_pods
from app.engine.deployment_topology_modeling.live_state.ownership_resolver import resolve_deployment_ownership
from app.engine.deployment_topology_modeling.live_state.concentration_analyzer import analyze_replica_concentration

# Internal JSON Mapping and Transformation Dependencies
from app.engine.deployment_topology_modeling.graph_builder import DeploymentGraphBuilder


def get_live_deployment_graph(namespace: str = "default") -> DeploymentGraph:
    """
    Central Orchestrator for Deployment Topology Modeling.
    
    This function acts as the primary API bridge for Component 2. It wraps all domain complexities
    behind a single facade and guarantees that the returned object aligns perfectly with the 
    shared Pydantic integration contract.
    """
    start_time = time.perf_counter()
    
    # Query the Kubernetes Control Plane to determine the active physical topology.
    # This evaluates all available hardware (Worker Nodes) across various failure zones.
    raw_nodes = get_cluster_nodes()
    raw_pods = get_cluster_pods(namespace=namespace)

    # Extract running Pod instances and trace their OwnerReferences back to logical Services.
    # This resolves the many-to-one relationship between scattered pods and a Deployment.
    workloads = resolve_deployment_ownership(pods=raw_pods, namespace=namespace)

    # Perform safety analysis to determine if workloads are critically concentrated.
    # This algorithm checks if too many replicas of a single service share the exact same Node.
    analyzed_services = analyze_replica_concentration(workloads)

    # Maintain modular boundaries by using a strictly-typed Builder pattern.
    # The builder translates internal Python dictionary structures into standard Pydantic models.
    builder = DeploymentGraphBuilder()
    
    # Directly attach our strongly typed Dataclasses (no messy dictionaries required)
    builder.attach_nodes(raw_nodes)
    
    # Advanced drift and state tracking analytics are decoupled and conditionally injected.
    baseline_drift_context = {}
    builder.attach_services(analyzed_services, baseline_drift_context)
    
    # Calculate exactly how long Component 2 took natively
    elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
    
    return builder.build(runtime_overhead_ms=elapsed_ms)


def get_sample_deployment() -> DeploymentGraph:
    """Provides fallback static JSON mocking for independent UI interface testing."""
    project_root = Path(__file__).resolve().parents[3]
    sample_file = project_root / "sample_data" / "deployment.json"

    if sample_file.exists():
        with open(sample_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return DeploymentGraph.model_validate(data)

    return DeploymentGraph(nodes=[], services=[])


if __name__ == "__main__":
    print("--- LIVE DEPLOYMENT GRAPH MODEL (ENTERPRISE EDITION) ---")
    graph = get_live_deployment_graph()
    print(f"Total Worker Nodes Resolved: {len(graph.nodes)}")
    print(f"Total Active Services Found: {len(graph.services)}")