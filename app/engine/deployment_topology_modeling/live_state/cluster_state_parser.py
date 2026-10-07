from kubernetes import client, config
from typing import List, Any
from app.engine.deployment_topology_modeling.live_state.cluster_models import ClusterNode, ClusterPod


def get_cluster_nodes() -> List[ClusterNode]:
    """Fetch all nodes and their basic metadata from the live Kubernetes cluster."""
    config.load_kube_config()
    v1 = client.CoreV1Api()

    nodes_data = []
    nodes = v1.list_node().items

    for node in nodes:
        labels = node.metadata.labels or {}
        
        # Exclude control-plane nodes
        if "node-role.kubernetes.io/control-plane" in labels or "node-role.kubernetes.io/master" in labels:
            continue
            
        name = node.metadata.name
        zone = labels.get("topology.kubernetes.io/zone", "default-zone")

        # Extract allocatable computing capacity
        allocatable = node.status.allocatable or {}
        cpu = allocatable.get("cpu", "0")
        memory = allocatable.get("memory", "0")

        nodes_data.append(ClusterNode(
            name=name,
            zone=zone,
            cpu_available=cpu,
            memory_available=memory
        ))

    return nodes_data


def get_cluster_pods(namespace: str = "default") -> List[ClusterPod]:
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

        pods_data.append(ClusterPod(
            name=pod_name,
            node=node_name,
            ready=is_ready,
            owner_kind=owner_kind,
            owner_name=owner_name,
            app_label=app_label,
            labels=labels
        ))

    return pods_data


if __name__ == "__main__":
    print("--- LIVE NODES ---")
    nodes = get_cluster_nodes()
    for n in nodes:
        print(f"Node: {n.name} (Zone: {n.zone})")

    print("\n--- LIVE PODS (First 5) ---")
    pods = get_cluster_pods()
    for p in pods[:5]:
        print(f"Pod: {p.name} -> Node: {p.node} (Ready: {p.ready}, App: {p.app_label})")