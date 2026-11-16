"""The probe entry point: recognised and reported read-only, and kept out of the application's topology."""

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.engine.deployment_topology_modeling.discovery import discover
from app.integrations.kubernetes.probe_entry import MANIFEST_PATH, PROBE_ENTRY_LABEL, describe_entry_points, without_probe_entries
from app.integrations.probe.http_fetch import allowed_probe_port
from app.main import app
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod, service

client = TestClient(app)
ROOT = Path(__file__).resolve().parents[2]


def entry_service(address: str | None = None, port: int = 8080, labelled: bool = True) -> dict:
    body = {"metadata": {"name": "resilience-probe-entry", "namespace": "shop", "labels": {PROBE_ENTRY_LABEL: "true"} if labelled else {}},
            "spec": {"type": "LoadBalancer", "selector": {"app": "web"}, "ports": [{"port": port, "targetPort": 9080}]}}
    if address:
        body["status"] = {"loadBalancer": {"ingress": [{"ip": address}]}}
    return body


class WithEntry(FakeClusterSource):
    """The fake cluster plus the entry Service a person would have created."""
    extra: list[dict] = []

    def list_objects(self, resource, path):
        items = super().list_objects(resource, path)
        return [*items, *self.extra] if resource == "services" and path.endswith("/shop/services") else items


def shop(extra=()) -> WithEntry:
    cluster = WithEntry(nodes=[node("w1"), node("w2")], pods={"shop": [pod("web-1", "w1", app="web"), pod("web-2", "w2", app="web")]},
                        deployments={"shop": [deployment("web", 2)]}, services={"shop": [service("web", {"app": "web"})]})
    cluster.extra = list(extra)
    return cluster


def test_the_manifest_is_one_labelled_loopback_reachable_service_and_nothing_else():
    documents = list(yaml.safe_load_all((ROOT / MANIFEST_PATH).read_text(encoding="utf-8")))
    assert len(documents) == 1, "one Service, no Deployment, no pod, no change to an existing object"
    manifest = documents[0]
    assert manifest["kind"] == "Service" and manifest["spec"]["type"] == "LoadBalancer"
    assert manifest["metadata"]["labels"][PROBE_ENTRY_LABEL] == "true"
    assert manifest["spec"]["selector"] == {"app": "productpage"}
    assert allowed_probe_port(manifest["spec"]["ports"][0]["port"]), "the published port is one the probe may reach"


def test_a_labelled_service_is_the_entry_point_and_an_unlabelled_lookalike_is_not():
    services = [entry_service("172.18.255.200"), entry_service(labelled=False)]
    assert [e["address"] for e in describe_entry_points(services, allowed_probe_port)] == ["172.18.255.200"]
    assert len(without_probe_entries(services)) == 1


def test_the_entry_point_is_not_part_of_the_applications_topology(use_cluster):
    plain = discover("shop", source=use_cluster(shop()))
    with_entry = discover("shop", source=use_cluster(shop([entry_service("172.18.255.200")])))
    assert [s.name for s in with_entry.graph.services] == [s.name for s in plain.graph.services] == ["web"]
    assert with_entry.status == plain.status


def test_the_route_reports_none_pending_and_ready_and_reads_only(use_cluster):
    use_cluster(shop())
    assert client.get("/api/recommendations/validation-loop/entry-point", params={"namespace": "shop"}).json()["state"] == "none"
    use_cluster(shop([entry_service()]))
    body = client.get("/api/recommendations/validation-loop/entry-point", params={"namespace": "shop"}).json()
    assert body["state"] == "pending" and body["entries"][0]["url"] == "http://127.0.0.1:8080/" and body["entries"][0]["portAllowed"] is True
    use_cluster(shop([entry_service("172.18.255.200")]))
    body = client.get("/api/recommendations/validation-loop/entry-point", params={"namespace": "shop"}).json()
    assert body["state"] == "ready" and body["readOnly"] is True and body["entries"][0]["address"] == "172.18.255.200"
    assert body["applyCommand"].startswith("kubectl apply -f ")


def test_a_port_the_probe_may_not_reach_is_reported_as_not_allowed(use_cluster):
    use_cluster(shop([entry_service("1.2.3.4", port=9080)]))
    body = client.get("/api/recommendations/validation-loop/entry-point", params={"namespace": "shop"}).json()
    assert body["entries"][0]["portAllowed"] is False
