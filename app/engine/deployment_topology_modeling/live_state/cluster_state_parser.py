from kubernetes import client, config
from typing import Dict, List, Any


def get_cluster_nodes() -> List[Dict[str, Any]]:
    """Fetch all nodes and their basic metadata from the live Kubernetes cluster."""
    config.load_kube_config()
    v1 = client.CoreV1Api()

    nodes_data = []
    nodes = v1.list_node().items

    for node in nodes:
        name = node.metadata.name
        labels = node.metadata.labels or {}
        zone = labels.get("topology.kubernetes.io/zone", "default-zone")

        # Extract allocatable computing capacity
        allocatable = node.status.allocatable or {}
        cpu = allocatable.get("cpu", "0")
        memory = allocatable.get("memory", "0")

        nodes_data.append({
            "name": name,
            "zone": zone,
            "cpuAvailable": cpu,
            "memoryAvailable": memory,
        })

    return nodes_data


def get_cluster_pods(namespace: str = "default") -> List[Dict[str, Any]]:
    """Fetch live pods and readiness status within the specified namespace."""
    config.load_kube_config()
    v1 = client.CoreV1Api()

    pods_data = []
    pods = v1.list_namespaced_pod(namespace=namespace).items

    for pod in pods:
        pod_name = pod.metadata.name
        node_name = pod.spec.node_name

        # Verify pod readiness condition
        is_ready = False
        if pod.status.conditions:
            for cond in pod.status.conditions:
                if cond.type == "Ready" and cond.status == "True":
                    is_ready = True
                    break

        # Extract primary owner reference (typically ReplicaSet)
        owner_name = None
        owner_kind = None
        if pod.metadata.owner_references:
            owner = pod.metadata.owner_references[0]
            owner_name = owner.name
            owner_kind = owner.kind

        labels = pod.metadata.labels or {}
        app_label = labels.get("app", "")

        pods_data.append({
            "name": pod_name,
            "node": node_name,
            "ready": is_ready,
            "owner_kind": owner_kind,
            "owner_name": owner_name,
            "app_label": app_label,
        })

    return pods_data


if __name__ == "__main__":
    print("--- LIVE NODES ---")
    nodes = get_cluster_nodes()
    for n in nodes:
        print(f"Node: {n['name']} (Zone: {n['zone']})")

    print("\n--- LIVE PODS (First 5) ---")
    pods = get_cluster_pods()
    for p in pods[:5]:
        print(f"Pod: {p['name']} -> Node: {p['node']} (Ready: {p['ready']}, App: {p['app_label']})")