#!/usr/bin/env python3
"""Prepare an isolated acceptance bundle. Default preparation never contacts or changes Kubernetes."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "resilience-release-acceptance"
NAMESPACE = "resilience-release"
WORKLOAD_IMAGE = "nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236"
NODE_IMAGE = "kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5"


def prepare(output: Path, operator: str):
    import yaml
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    kind = {"kind": "Cluster", "apiVersion": "kind.x-k8s.io/v1alpha4", "nodes": [
        {"role": "control-plane"}, {"role": "worker"}, {"role": "worker"}]}
    objects = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE,
        "labels": {"resilience.release-acceptance": "true"}}}]
    for name in ("frontend", "backend"):
        objects.extend([
            {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "namespace": NAMESPACE},
             "spec": {"replicas": 1, "selector": {"matchLabels": {"app": name}}, "template": {
                 "metadata": {"labels": {"app": name}}, "spec": {"containers": [{"name": name,
                 "image": WORKLOAD_IMAGE, "ports": [{"containerPort": 80}],
                 "resources": {"requests": {"cpu": "50m", "memory": "32Mi"}, "limits": {"memory": "128Mi"}},
                 "readinessProbe": {"httpGet": {"path": "/", "port": 80}}}]}}}},
            {"apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "namespace": NAMESPACE},
             "spec": {"selector": {"app": name}, "ports": [{"port": 80, "targetPort": 80}]}}])
    (output / "kind.yaml").write_text(yaml.safe_dump(kind))
    (output / "workloads.yaml").write_text(yaml.safe_dump_all(objects))
    (output / "declared.json").write_text(json.dumps({"workflowId": "release-checkout", "criticality": 1,
        "declaredBy": operator, "mandatoryServices": ["frontend", "backend"], "optionalServices": [],
        "edges": [{"source": "frontend", "target": "backend"}],
        "note": "Acceptance declaration only; the nginx services do not demonstrate an observed business dependency."}, indent=2))
    plan = {"clusterName": NAME, "namespace": NAMESPACE, "nodeImage": NODE_IMAGE, "workloadImage": WORKLOAD_IMAGE,
            "operator": operator, "writesAuthorized": False, "faultInjectionAuthorized": False,
            "scenarios": ["approval/apply/rollout/keep/reset", "outside-edit refusal", "partial-reset retry",
                          "process crash after restore/recovery", "persisted evidence", "stale application refusal"]}
    (output / "plan.json").write_text(json.dumps(plan, indent=2))
    (output / "RUNBOOK.txt").write_text(f'''No cluster or workload has been created. The following actions require explicit user approval.
Run from this directory using the same Python environment as the project. Do not use the developer's default kubeconfig.
1. kind create cluster --name {NAME} --image {NODE_IMAGE} --config kind.yaml --kubeconfig kubeconfig
2. kubectl --kubeconfig kubeconfig --context kind-{NAME} apply -f workloads.yaml
3. kubectl --kubeconfig kubeconfig --context kind-{NAME} -n {NAMESPACE} rollout status deployment/frontend --timeout=180s
   Repeat for deployment/backend. Preserve logs if either fails.
4. kubectl --kubeconfig kubeconfig --context kind-{NAME} get namespace kube-system -o jsonpath='{{.metadata.uid}}'
5. python {ROOT / 'scripts/release_acceptance.py'} --bundle . --check --expected-uid THE_UID
6. After reviewing the read-only evidence and receiving explicit approval of the plan:
   python {ROOT / 'scripts/release_acceptance.py'} --bundle . --execute --expected-uid THE_UID
The execute command is not authorization by itself. It runs the real approval service with the configured operator in an isolated DB,
then actual Deployment Apply/Reset requests, one outside edit, simulated transport failures and a child-process crash during Reset.
It never stops a Kubernetes node. Node fault injection requires a separate reviewed validation-loop plan and approval of its digest/UID.
Database, kubeconfig, snapshots and test logs stay in this private directory. Do not publish them.
On failure do not delete the cluster or database: inspect evidence and reconcile/reset first.
Cleanup, only after separate approval: kind delete cluster --name {NAME} (do not remove any other cluster).
This suite proves application/service integration, not business dependency correctness, real node survival or native Docker UI behavior.
''')
    return plan


def check(bundle: Path, uid: str):
    sys.path.insert(0, str(ROOT))
    from app.integrations.kubernetes.validation_gateway import KubernetesTestClusterGateway
    gateway = KubernetesTestClusterGateway(str(bundle / "kubeconfig"), connected_cluster=True)
    identity = gateway.identity()
    if not uid or identity.cluster_uid != uid or identity.context != f"kind-{NAME}":
        raise ValueError("Explicit disposable-cluster context and UID must match; nothing was changed")
    if len(identity.node_names) != 3 or any(not n.startswith(NAME + "-") for n in identity.node_names):
        raise ValueError("Expected only the three named disposable kind nodes")
    ns = gateway._get(f"/api/v1/namespaces/{NAMESPACE}", "namespaces")
    if ns.get("metadata", {}).get("labels", {}).get("resilience.release-acceptance") != "true":
        raise ValueError("Acceptance namespace is not designated")
    deployments = gateway.read_deployments(NAMESPACE)
    if set(deployments) != {"frontend", "backend"}:
        raise ValueError("Acceptance namespace must contain only the two prepared Deployments")
    version = gateway._get("/version", "Kubernetes version")
    schema = gateway._get("/openapi/v3/api/v1", "Kubernetes core API schema")
    spread = schema["components"]["schemas"]["io.k8s.api.core.v1.TopologySpreadConstraint"]["properties"]
    required_fields = {"topologyKey", "maxSkew", "whenUnsatisfiable", "labelSelector", "nodeTaintsPolicy", "matchLabelKeys"}
    missing = sorted(required_fields - set(spread))
    if missing:
        raise ValueError("Server schema cannot represent the generated proposal: " + ", ".join(missing))
    return {"kubernetesVersion": version.get("gitVersion"), "proposalFieldsVerified": sorted(required_fields),
            "clusterUid": uid, "nodes": list(identity.node_names), "namespace": NAMESPACE, "writesPerformed": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--operator")
    p.add_argument("--expected-uid")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = p.parse_args(argv)
    bundle = args.bundle.resolve()
    if args.prepare:
        if not args.operator or len(args.operator.strip()) < 2:
            p.error("--operator is required for preparation (audit attribution, not authentication)")
        print(json.dumps(prepare(bundle, args.operator)))
        return 0
    evidence = check(bundle, args.expected_uid)
    (bundle / "preflight.json").write_text(json.dumps(evidence, indent=2))
    if args.check:
        print(json.dumps(evidence)); return 0
    env = {**os.environ, "RESILIENCE_TEST_KUBECONFIG": str(bundle / "kubeconfig"),
           "RESILIENCE_RELEASE_BUNDLE": str(bundle), "RESILIENCE_RELEASE_APPROVED_UID": args.expected_uid}
    return subprocess.run([sys.executable, "-m", "pytest", "-v", "tests/live_release_acceptance",
                           f"--junitxml={bundle / 'results.xml'}"], cwd=ROOT, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
