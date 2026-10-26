"""Component 1's outage scaler against a fake API server (``httpx.MockTransport``): opt-in, conditional PATCH, recovery record."""

import base64
import json
from pathlib import Path

import httpx
import pytest

from app.integrations.kubernetes.workflow_outage import (
    ORIGINAL_REPLICAS_ANNOTATION,
    KubernetesDeploymentScaler,
    OutageClusterError,
    OutageNotPermittedError,
)

NS = "resilience-workflow-demo"
CA_B64 = base64.b64encode((Path(__file__).resolve().parents[1] / "fixtures" / "tls" / "ca.pem").read_bytes()).decode()


def write_kubeconfig(path):
    path.write_text(
        "apiVersion: v1\nkind: Config\ncurrent-context: c\n"
        f"clusters:\n- name: k\n  cluster:\n    server: https://127.0.0.1:1\n    certificate-authority-data: {CA_B64}\n"
        "users:\n- name: u\n  user:\n    token: test-token\n"
        "contexts:\n- name: c\n  context:\n    cluster: k\n    user: u\n"
    )
    return str(path)


class FakeApi:
    """One namespace with Deployments whose ready pods follow spec.replicas at once."""

    def __init__(self, opted_in=True, replicas=1):
        self.labels = {"resilience.research/workflow-outages": "allowed"} if opted_in else {}
        self.deployments = {
            "emailservice": {"metadata": {"name": "emailservice", "uid": "uid-email", "resourceVersion": "7", "annotations": {}},
                             "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": "emailservice"}}},
                             "status": {"readyReplicas": replicas}},
        }
        self.patches = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/api/v1/namespaces/{NS}":
            return httpx.Response(200, json={"metadata": {"name": NS, "labels": self.labels}})
        if path == f"/apis/apps/v1/namespaces/{NS}/deployments":
            return httpx.Response(200, json={"items": list(self.deployments.values())})
        if path == f"/api/v1/namespaces/{NS}/pods":
            app = request.url.params["labelSelector"].split("=", 1)[1]
            count = self.deployments[app]["status"].get("readyReplicas") or 0
            return httpx.Response(200, json={"items": [{"metadata": {"name": f"{app}-{i}"}} for i in range(count)]})
        name = path.rsplit("/", 1)[-1]
        deployment = self.deployments.get(name)
        if deployment is None:
            return httpx.Response(404, json={})
        if request.method == "GET":
            return httpx.Response(200, json=deployment)
        assert request.method == "PATCH"
        assert request.headers["content-type"] == "application/strategic-merge-patch+json"
        body = json.loads(request.content)
        self.patches.append(body)
        if body["metadata"]["resourceVersion"] != deployment["metadata"]["resourceVersion"]:
            return httpx.Response(409, json={})
        for key, value in body["metadata"].get("annotations", {}).items():
            if value is None:
                deployment["metadata"]["annotations"].pop(key, None)
            else:
                deployment["metadata"]["annotations"][key] = value
        deployment["spec"]["replicas"] = body["spec"]["replicas"]
        deployment["status"]["readyReplicas"] = body["spec"]["replicas"]
        deployment["metadata"]["resourceVersion"] = str(int(deployment["metadata"]["resourceVersion"]) + 1)
        return httpx.Response(200, json=deployment)


def scaler(tmp_path, api):
    return KubernetesDeploymentScaler(NS, config_file=write_kubeconfig(tmp_path / "cfg"), transport=httpx.MockTransport(api))


def test_a_namespace_without_the_opt_in_label_is_refused(tmp_path):
    with pytest.raises(OutageNotPermittedError, match="has not opted in"):
        scaler(tmp_path, FakeApi(opted_in=False)).require_opt_in()


def test_take_down_records_the_replica_count_and_is_conditional_on_the_version_read(tmp_path):
    api = FakeApi(replicas=2)
    cluster = scaler(tmp_path, api)
    cluster.require_opt_in()
    before = cluster.inspect("emailservice")
    assert before.restored and not before.down and before.pod_count == 2

    cluster.take_down(before)

    assert api.patches == [{"metadata": {"resourceVersion": "7", "annotations": {ORIGINAL_REPLICAS_ANNOTATION: "2"}},
                            "spec": {"replicas": 0}}]
    after = cluster.inspect("emailservice")
    assert after.down and after.original_replicas == 2
    assert "specReplicas=0" in after.evidence() and "matchingPods=0" in after.evidence()
    assert cluster.interrupted() == ["emailservice"]


def test_restore_puts_back_the_recorded_count_and_removes_the_record(tmp_path):
    api = FakeApi(replicas=2)
    cluster = scaler(tmp_path, api)
    cluster.take_down(cluster.inspect("emailservice"))

    assert cluster.restore(cluster.inspect("emailservice")) == 2

    assert api.patches[-1]["metadata"]["annotations"] == {ORIGINAL_REPLICAS_ANNOTATION: None}
    assert api.patches[-1]["spec"] == {"replicas": 2}
    assert cluster.inspect("emailservice").restored
    assert cluster.interrupted() == []


def test_a_deployment_changed_since_it_was_read_is_not_overwritten(tmp_path):
    api = FakeApi()
    cluster = scaler(tmp_path, api)
    stale = cluster.inspect("emailservice")
    api.deployments["emailservice"]["metadata"]["resourceVersion"] = "8"
    with pytest.raises(OutageClusterError, match="changed"):
        cluster.take_down(stale)
    assert api.deployments["emailservice"]["spec"]["replicas"] == 1


def test_an_unrecovered_outage_cannot_be_started_twice_and_a_missing_deployment_is_refused(tmp_path):
    cluster = scaler(tmp_path, FakeApi())
    cluster.take_down(cluster.inspect("emailservice"))
    with pytest.raises(OutageNotPermittedError, match="unrecovered"):
        cluster.take_down(cluster.inspect("emailservice"))
    with pytest.raises(OutageNotPermittedError, match="does not exist"):
        cluster.inspect("not-a-service")


def test_no_namespace_configured_is_refused():
    with pytest.raises(OutageNotPermittedError):
        KubernetesDeploymentScaler("  ")
