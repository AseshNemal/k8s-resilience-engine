from collections import Counter
from typing import Dict, List, Any
from app.engine.deployment_topology_modeling.live_state.ownership_resolver import resolve_deployment_ownership


def analyze_replica_concentration(workloads: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Analyze replica concentration and single-node failure exposure for each service.
    Calculates the concentration ratio and identifies nodes that cause complete outage if lost.
    """
    analyzed_services = []

    for name, data in workloads.items():
        ready_replicas = data["readyReplicas"]
        declared_replicas = data["declaredReplicas"]
        pods = data["pods"]

        # Track node distribution for ready pods
        ready_pod_nodes = [p["node"] for p in pods if p["ready"] and p["node"]]

        if not ready_pod_nodes:
            # No ready pods available
            concentration = 0.0
            exposed_nodes = []
        else:
            node_counts = Counter(ready_pod_nodes)
            max_pods_on_single_node = max(node_counts.values())
            
            # Concentration ratio: max pods on one node / total ready pods
            concentration = round(max_pods_on_single_node / len(ready_pod_nodes), 2)

            # A node is exposed if losing it removes all ready pods
            exposed_nodes = [
                node for node, count in node_counts.items()
                if count == len(ready_pod_nodes)
            ]

        analyzed_services.append({
            "name": name,
            "declaredReplicas": declared_replicas,
            "readyReplicas": ready_replicas,
            "replicaConcentration": concentration,
            "exposedNodes": exposed_nodes,
            "pods": pods
        })

    return analyzed_services


if __name__ == "__main__":
    from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import get_cluster_pods
    print("--- REPLICA CONCENTRATION ANALYSIS ---")
    live_pods = get_cluster_pods()
    workloads = resolve_deployment_ownership(live_pods)
    results = analyze_replica_concentration(workloads)

    for svc in results:
        if svc["pods"]:
            print(f"Service: {svc['name']}")
            print(f"  Ready Replicas:        {svc['readyReplicas']} / {svc['declaredReplicas']}")
            print(f"  Replica Concentration: {svc['replicaConcentration']}")
            print(f"  Exposed to Node(s):    {svc['exposedNodes']}")
            print("-" * 40)