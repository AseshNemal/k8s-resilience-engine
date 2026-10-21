"""The stored KinD snapshot from experiments/single-node-failure/concentrated-baseline.

Observed facts (kubectl output in that folder): 3 Ready paymentservice pods, all on node
resilience-research-worker; 3 worker nodes + 1 control-plane node; namespace resilience-test.

The snapshot holds no ReplicaSet object, so one is CONSTRUCTED here from what the snapshot does
contain: the pods' controller ownerReference (ReplicaSet paymentservice-74d644658f and its UID)
and the Deployment's UID in paymentservice-deployment.yaml.
"""
import json
from pathlib import Path

import yaml

from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import (
    nodes_from_objects,
    pods_from_objects,
)
from app.engine.deployment_topology_modeling.live_state.concentration_analyzer import analyze_replica_concentration
from app.engine.deployment_topology_modeling.live_state.ownership_resolver import resolve_service_ownership

SNAPSHOT_DIR = (
    Path(__file__).resolve().parents[2] / "experiments" / "single-node-failure" / "concentrated-baseline"
)
NAMESPACE = "resilience-test"
CONCENTRATED_NODE = "resilience-research-worker"


def pod_objects() -> list[dict]:
    return json.loads((SNAPSHOT_DIR / "pods.json").read_text())["items"]


def node_objects() -> list[dict]:
    return json.loads((SNAPSHOT_DIR / "nodes.json").read_text())["items"]


def service_objects() -> list[dict]:
    return [yaml.safe_load((SNAPSHOT_DIR / "paymentservice-service.yaml").read_text())]


def deployment_objects() -> list[dict]:
    return [yaml.safe_load((SNAPSHOT_DIR / "paymentservice-deployment.yaml").read_text())]


def replica_set_objects() -> list[dict]:
    owner = pod_objects()[0]["metadata"]["ownerReferences"][0]
    deployment = deployment_objects()[0]
    return [{
        "metadata": {
            "name": owner["name"],
            "uid": owner["uid"],
            "namespace": NAMESPACE,
            "ownerReferences": [{
                "kind": "Deployment",
                "name": deployment["metadata"]["name"],
                "uid": deployment["metadata"]["uid"],
                "controller": True,
            }],
        }
    }]


def collect(namespace: str = NAMESPACE):
    """Drop-in replacement for service.collect_live_cluster, fed from the stored snapshot."""
    nodes = nodes_from_objects(node_objects(), pod_objects())
    pods = pods_from_objects(pod_objects(), NAMESPACE)
    workloads = resolve_service_ownership(
        pods, service_objects(), replica_set_objects(), deployment_objects(), NAMESPACE
    )
    return nodes, analyze_replica_concentration(workloads, {n.name: n.ready for n in nodes})
