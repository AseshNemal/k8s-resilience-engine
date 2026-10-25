"""Writes the two Bookinfo experiment configurations (run once; output is version-controlled).

The real-application check of the live placement-drift experiments. The workloads are the four v1
services of the team's Bookinfo benchmark (github.com/AseshNemal/k8s-bookinfo, platform/kube/bookinfo.yaml,
the "version!=v2,version!=v3" selection of the setup guide), with the same images, ports and volumes, in
their own namespace so the team's `bookinfo` namespace is never touched by an experiment.

Differences from the benchmark manifest, each needed by the experiment and nothing else:
- 3 replicas per Deployment instead of 1 (with 1 replica a service is always exposed, so drift cannot change);
- CPU/memory requests of 100m / 128Mi on every container (the benchmark sets none, and the scheduler only
  counts requests, so the constrained-resources scenario could not constrain anything); no limits;
- the label experiment=placement-drift, which the runner uses to find and clean up its own pods;
- automountServiceAccountToken: false (Bookinfo never calls the Kubernetes API; scheduling is unaffected).

Config A (no-rule):  replicas and requests only - placement left to the default scheduler.
Config B (spread):   the same plus topologySpreadConstraints maxSkew=1 on kubernetes.io/hostname,
                     whenUnsatisfiable=DoNotSchedule (identical to the stand-in config B).
"""
from pathlib import Path

import yaml

NAMESPACE = "bookinfo-exp"
IMAGE_TAG = "1.20.3"
# 3 copies, not Bookinfo's 1: with one copy a service is always exposed, so there would be no drift to see.
REPLICAS = 3
# The scheduler only looks at requests when it checks whether a pod fits on a node,
# so without them a node could never be "full" (needed for the constrained-resources scenario).
REQUESTS = {"cpu": "100m", "memory": "128Mi"}
PORT = 9080
# Lets the runner find and clean up its own objects.
EXPERIMENT_LABEL = {"experiment": "placement-drift"}
OUT_DIR = Path(__file__).resolve().parents[3] / "deploy" / "experiments" / "placement-drift-bookinfo"

# Kubernetes Service name -> Deployment name (Bookinfo names its Deployments <service>-v1).
SERVICES = {"details": "details-v1", "ratings": "ratings-v1", "reviews": "reviews-v1", "productpage": "productpage-v1"}

# Container settings copied from the benchmark manifest.
_REVIEWS_EXTRA = {
    "env": [{"name": "LOG_DIR", "value": "/tmp/logs"}],
    "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}, {"name": "wlp-output", "mountPath": "/opt/ibm/wlp/output"}],
}
_PRODUCTPAGE_EXTRA = {"volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}]}
_VOLUMES = {
    "reviews": [{"name": "wlp-output", "emptyDir": {}}, {"name": "tmp", "emptyDir": {}}],
    "productpage": [{"name": "tmp", "emptyDir": {}}],
}
_EXTRA = {"reviews": _REVIEWS_EXTRA, "productpage": _PRODUCTPAGE_EXTRA}


def _labels(service: str) -> dict:
    return {"app": service, "version": "v1"}


def _deployment(service: str, spread: bool) -> dict:
    container = {
        "name": service,
        "image": f"registry.istio.io/release/examples-bookinfo-{service}-v1:{IMAGE_TAG}",
        "imagePullPolicy": "IfNotPresent",
        "ports": [{"containerPort": PORT}],
        "resources": {"requests": dict(REQUESTS)},
        **_EXTRA.get(service, {}),
    }
    pod_spec = {
        "serviceAccountName": f"bookinfo-{service}",
        "automountServiceAccountToken": False,
        "containers": [container],
    }
    if service in _VOLUMES:
        pod_spec["volumes"] = _VOLUMES[service]
    # Config B only (the one difference between the configs): keep the copies evenly spread over nodes,
    # at most 1 apart, and leave a pod Pending rather than break that.
    if spread:
        pod_spec["topologySpreadConstraints"] = [{
            "maxSkew": 1,
            "topologyKey": "kubernetes.io/hostname",
            "whenUnsatisfiable": "DoNotSchedule",
            "labelSelector": {"matchLabels": _labels(service)},
        }]
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": SERVICES[service], "namespace": NAMESPACE, "labels": {**_labels(service), **EXPERIMENT_LABEL}},
        "spec": {
            "replicas": REPLICAS,
            "selector": {"matchLabels": _labels(service)},
            "template": {"metadata": {"labels": {**_labels(service), **EXPERIMENT_LABEL}}, "spec": pod_spec},
        },
    }


def _service(service: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": service, "namespace": NAMESPACE, "labels": {"app": service, "service": service, **EXPERIMENT_LABEL}},
        "spec": {"selector": {"app": service}, "ports": [{"port": PORT, "name": "http"}]},
    }


def _service_account(service: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": f"bookinfo-{service}", "namespace": NAMESPACE, "labels": dict(EXPERIMENT_LABEL)},
        "automountServiceAccountToken": False,
    }


def documents(spread: bool) -> list[dict]:
    docs = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}}]
    for service in SERVICES:
        docs += [_service(service), _service_account(service), _deployment(service, spread)]
    return docs


def write(config_name: str, spread: bool) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{config_name}.yaml"
    header = (f"# Generated by experiments/placement_drift/live/bookinfo_workloads.py - config {config_name}\n"
              f"# Bookinfo v1 ({IMAGE_TAG}) in namespace {NAMESPACE}: {REPLICAS} replicas, requests "
              f"{REQUESTS['cpu']}/{REQUESTS['memory']}, no limits.\n")
    path.write_text(header + yaml.safe_dump_all(documents(spread), sort_keys=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    print(write("config-a-no-rule", spread=False))
    print(write("config-b-topology-spread", spread=True))
