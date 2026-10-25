"""Prepare (never apply) the instrumented Boutique Kubernetes demo for review.

Uses the repository's resolved Compose configuration and installed image IDs.
Run from any directory with Python, PyYAML and Docker available. The generated
namespace is isolated; this tool does not call kubectl, kind, start or stop.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "resilience-workflow-demo"
OUT = ROOT / "deploy" / "kubernetes" / "workflow-demo"


def docker_json(*args: str):
    return json.loads(subprocess.check_output(["docker", *args], cwd=ROOT, text=True))


def main() -> None:
    config = docker_json(
        "compose", "-f", "deploy/docker-compose.boutique.yml", "-f",
        "deploy/docker-compose.boutique-instrumented.yml", "config", "--format", "json",
    )
    documents = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}}]
    documents.append({"apiVersion": "v1", "kind": "PersistentVolumeClaim",
        "metadata": {"name": "jaeger-traces", "namespace": NAMESPACE},
        "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}}}})
    images = []
    for name, service in sorted(config["services"].items()):
        if name == "loadgenerator":
            continue  # Requests are initiated explicitly by the operator.
        image = service["image"]
        metadata = docker_json("image", "inspect", image)[0]
        images.append({"service": name, "image": image, "imageId": metadata["Id"],
                       "repoDigests": metadata.get("RepoDigests", [])})
        ports = {"frontend": [8080], "cartservice": [7070], "redis-cart": [6379],
                 "productcatalogservice": [3550], "currencyservice": [7000],
                 "paymentservice": [50051], "shippingservice": [50051],
                 "emailservice": [8080], "checkoutservice": [5050],
                 "recommendationservice": [8080], "adservice": [9555],
                 "jaeger": [16686, 4317, 4318]}[name]
        labels = {"app": name, "app.kubernetes.io/part-of": "resilience-workflow-demo"}
        container = {
            "name": name, "image": image, "imagePullPolicy": "Never",
            "env": [{"name": key, "value": str(value)}
                    for key, value in sorted(service.get("environment", {}).items())],
            "ports": [{"containerPort": port} for port in ports],
            "resources": {"requests": {"cpu": "50m", "memory": "256Mi" if name == "adservice" else "64Mi"},
                          "limits": {"cpu": "1000m", "memory": "768Mi" if name == "adservice" else "512Mi"}},
            "readinessProbe": {"tcpSocket": {"port": ports[0]}, "initialDelaySeconds": 5, "periodSeconds": 5},
        }
        pod_spec = {"automountServiceAccountToken": False, "containers": [container]}
        if name == "jaeger":
            # Official Jaeger all-in-one Badger storage. A pod restart must not
            # erase the only copy of collected traces during the demonstration.
            container["env"].extend({"name": key, "value": value} for key, value in {
                "SPAN_STORAGE_TYPE": "badger", "BADGER_EPHEMERAL": "false",
                "BADGER_DIRECTORY_VALUE": "/badger/data", "BADGER_DIRECTORY_KEY": "/badger/key",
            }.items())
            container["volumeMounts"] = [{"name": "traces", "mountPath": "/badger"}]
            pod_spec["volumes"] = [{"name": "traces", "persistentVolumeClaim": {"claimName": "jaeger-traces"}}]
            pod_spec["securityContext"] = {"fsGroup": 10001}
        documents.append({"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "spec": {"replicas": 1, "selector": {"matchLabels": {"app": name}},
                     "template": {"metadata": {"labels": labels,
                         "annotations": {"resilience-research/image-id": metadata["Id"]}},
                         "spec": pod_spec}}})
        if name == "jaeger":
            documents[-1]["spec"]["strategy"] = {"type": "Recreate"}
        documents.append({"apiVersion": "v1", "kind": "Service",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "spec": {"selector": {"app": name}, "ports": [
                {"name": f"tcp-{port}", "port": port, "targetPort": port} for port in ports]}})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "manifest.yaml").write_text(yaml.safe_dump_all(documents, sort_keys=False), encoding="utf-8")
    (OUT / "images.json").write_text(json.dumps(images, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(images)} deployments and services in {OUT}")
    print("No cluster changes made. Review manifest.yaml and images.json before approval.")


if __name__ == "__main__":
    main()
