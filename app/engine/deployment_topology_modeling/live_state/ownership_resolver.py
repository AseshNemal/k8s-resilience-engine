from kubernetes import client, config
from typing import Dict, List, Any
from app.engine.deployment_topology_modeling.live_state.cluster_models import ClusterPod


def resolve_deployment_ownership(pods: List[ClusterPod], namespace: str = "default") -> Dict[str, Dict[str, Any]]:
    """
    Resolve the relationship between Deployments and Pods using typed ClusterPod dataclasses.
    Maps each workload to its declared replicas, ready replicas, and hosted nodes.
    """
    config.load_kube_config()
    apps_v1 = client.AppsV1Api()

    # 1. Fetch live deployments to identify declared replica counts
    deployments = apps_v1.list_namespaced_deployment(namespace=namespace).items
    deployment_map: Dict[str, Dict[str, Any]] = {}

    for dep in deployments:
        dep_name = dep.metadata.name
        declared_replicas = dep.spec.replicas if dep.spec.replicas is not None else 1
        
        deployment_map[dep_name] = {
            "name": dep_name,
            "declaredReplicas": declared_replicas,
            "readyReplicas": 0,
            "pods": []
        }

    # 2. Map each ClusterPod back to its parent deployment
    for pod in pods:
        app_name = pod.app_label

        # Associate pod with corresponding deployment
        if app_name and app_name in deployment_map:
            deployment_map[app_name]["pods"].append({
                "name": pod.name,
                "node": pod.node,
                "ready": pod.ready
            })
            if pod.ready:
                deployment_map[app_name]["readyReplicas"] += 1

    return deployment_map


if __name__ == "__main__":
    from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import get_cluster_pods
    print("--- RESOLVED WORKLOAD OWNERSHIP ---")
    live_pods = get_cluster_pods()
    ownership = resolve_deployment_ownership(live_pods)
    for name, data in ownership.items():
        if data["pods"]:
            print(f"Service: {name}")
            print(f"  Declared Replicas: {data['declaredReplicas']}")
            print(f"  Ready Replicas:    {data['readyReplicas']}")
            for p in data["pods"]:
                print(f"    - Pod: {p['name']} on Node: {p['node']} (Ready: {p['ready']})")