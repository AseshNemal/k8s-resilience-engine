"""The eight disruption scenarios on the connected cluster's real topology (not the scripted placements)."""

import pytest
from fastapi.testclient import TestClient

from app.engine.deployment_topology_modeling.discovery import discover
from app.main import app
from experiments.placement_drift.constants import ScenarioStatus
from experiments.placement_drift.disruption_orchestrator import DisruptionOrchestrator
from experiments.placement_drift.live_topology_scenarios import choose_target_service
from experiments.placement_drift.snapshot_repository import SnapshotRepository
from tests.support.fake_cluster import FakeClusterSource, bookinfo, deployment, node, pod, two_node_shop

client = TestClient(app)

SCRIPTED_NAMES = {"worker-1", "worker-2", "worker-3", "paymentservice", "payment-1", "cartservice", "checkoutservice"}


def graph_of(source, namespace):
    return discover(namespace, source=source).graph


def run_all(source, namespace):
    repo = SnapshotRepository()
    graph = graph_of(source, namespace)
    orchestrator = DisruptionOrchestrator(namespace=namespace, repository=repo, topology=graph)
    return orchestrator.run_all(), repo, graph


def concentrated_payments():
    """paymentservice with 3 replicas on one worker, plus a spread web service."""
    return FakeClusterSource(
        nodes=[node("w1", "zone-a"), node("w2", "zone-b"), node("w3", "zone-c")],
        pods={"pay": [pod(f"payment-{i}", "w1", app="paymentservice") for i in range(3)]
                     + [pod("web-1", "w2", app="web"), pod("web-2", "w3", app="web")]},
        deployments={"pay": [deployment("paymentservice", 3), deployment("web", 2)]},
    )


def test_all_eight_scenarios_run_on_the_real_topology_and_say_so():
    results, _, _ = run_all(bookinfo(), "bookinfo")

    assert [r.scenario_id for r in results] == [
        "01_initial_deployment", "02_scale_up_stress", "03_scale_down_loss", "04_rolling_update",
        "05_pod_deletion_chaos", "06_node_cordon", "07_node_drain_eviction", "08_resource_contention",
    ]
    assert {r.status for r in results} == {ScenarioStatus.COMPLETED}
    assert {r.topology_source for r in results} == {"live"}


def test_the_snapshots_contain_only_the_clusters_real_nodes_and_services():
    results, repo, graph = run_all(bookinfo(), "bookinfo")

    real_nodes = {n.name for n in graph.nodes}
    real_services = {s.name for s in graph.services}
    for result in results:
        for phase in ("T0", "T1"):
            snapshot = repo.get(result.scenario_id, phase)
            assert {p.node_name for p in snapshot.pods} <= real_nodes
            assert {p.service_name for p in snapshot.pods} <= real_services
            assert not SCRIPTED_NAMES & ({p.node_name for p in snapshot.pods} | {p.service_name for p in snapshot.pods})


def test_the_baseline_is_exactly_the_real_placement():
    results, repo, graph = run_all(two_node_shop(), "shop")

    real = {(p.name, p.node) for s in graph.services for p in s.pods}
    for result in results:
        t0 = repo.get(result.scenario_id, "T0")
        assert {(p.pod_name, p.node_name) for p in t0.pods} == real


def test_a_run_is_reproducible():
    first, _, _ = run_all(concentrated_payments(), "pay")
    second, _, _ = run_all(concentrated_payments(), "pay")

    assert [(r.scenario_id, r.baseline_hash, r.target_hash, r.d_rate) for r in first] == \
           [(r.scenario_id, r.baseline_hash, r.target_hash, r.d_rate) for r in second]


def test_the_real_graph_is_never_changed():
    graph = graph_of(concentrated_payments(), "pay")
    before = graph.model_dump()

    DisruptionOrchestrator(namespace="pay", topology=graph).run_all()

    assert graph.model_dump() == before


def test_the_target_is_the_most_concentrated_service():
    assert choose_target_service(graph_of(concentrated_payments(), "pay")) == "paymentservice"


def test_a_node_drain_leaves_nothing_on_the_drained_node():
    results, repo, _ = run_all(concentrated_payments(), "pay")

    drain = next(r for r in results if r.scenario_id == "07_node_drain_eviction")
    t1 = repo.get("07_node_drain_eviction", "T1")
    assert {p.node_name for p in t1.pods} == {"w2", "w3"}  # w1 held the three payment pods
    assert len(t1.pods) == len(repo.get("07_node_drain_eviction", "T0").pods)
    assert "paymentservice" in drain.drifted_services


def test_scaling_and_replacement_change_the_pod_count_as_named():
    _, repo, _ = run_all(concentrated_payments(), "pay")

    def count(scenario, phase):
        return len(repo.get(scenario, phase).pods)

    assert count("02_scale_up_stress", "T1") == count("02_scale_up_stress", "T0") + 1
    assert count("03_scale_down_loss", "T1") == count("03_scale_down_loss", "T0") - 1
    assert count("04_rolling_update", "T1") == count("04_rolling_update", "T0")
    assert count("05_pod_deletion_chaos", "T1") == count("05_pod_deletion_chaos", "T0")
    assert count("01_initial_deployment", "T1") == count("01_initial_deployment", "T0")


def test_a_pod_the_scheduler_has_not_placed_is_not_given_a_node():
    source = two_node_shop()
    source.pods["shop"].append(pod("web-3", None, app="web", ready=False, phase="Pending"))

    _, repo, _ = run_all(source, "shop")

    assert "web-3" not in {p.pod_name for p in repo.get("02_scale_up_stress", "T0").pods}


def test_resource_contention_stops_when_a_node_did_not_report_its_cpu():
    source = two_node_shop()
    source.nodes[0] = node("worker-1", "zone-a", cpu=None)

    results, _, _ = run_all(source, "shop")

    contention = next(r for r in results if r.scenario_id == "08_resource_contention")
    assert contention.status == ScenarioStatus.FAILED
    assert "did not report" in contention.error_message
    # The other seven are unaffected by the missing figure.
    assert all(r.status == ScenarioStatus.COMPLETED for r in results if r is not contention)


def test_a_protected_namespace_is_refused_by_the_safety_guard():
    source = FakeClusterSource(
        nodes=[node("w1"), node("w2")],
        pods={"kube-system": [pod("coredns-1", "w1", app="coredns"), pod("coredns-2", "w2", app="coredns")]},
        deployments={"kube-system": [deployment("coredns", 2)]},
    )

    results, _, _ = run_all(source, "kube-system")

    assert {r.status for r in results} == {ScenarioStatus.ABORTED_BY_SAFETY}


# ---- through the API ------------------------------------------------------------------------------------

def test_the_benchmark_route_uses_the_real_cluster_by_default(use_cluster):
    use_cluster(two_node_shop())

    payload = client.get("/api/deployments/chaos/benchmarks", params={"namespace": "shop"}).json()

    assert payload["aggregate_metrics"]["topology_source"] == "live"
    assert {r["topology_source"] for r in payload["scenario_results"]} == {"live"}
    assert len(payload["scenario_results"]) == 8


def test_the_scripted_placements_are_still_available_and_labelled(use_cluster):
    use_cluster(two_node_shop())

    payload = client.get("/api/deployments/chaos/benchmarks", params={"namespace": "shop", "source": "scripted"}).json()

    assert payload["aggregate_metrics"]["topology_source"] == "scripted"
    assert {r["topology_source"] for r in payload["scenario_results"]} == {"scripted"}


def test_running_one_scenario_and_its_matrix_use_the_real_nodes(use_cluster):
    use_cluster(concentrated_payments())

    run = client.post("/api/deployments/chaos/run/07_node_drain_eviction", params={"namespace": "pay"})
    matrix = client.get("/api/deployments/chaos/matrix/07_node_drain_eviction", params={"namespace": "pay"})

    assert run.status_code == 200 and run.json()["topology_source"] == "live"
    assert matrix.status_code == 200
    assert set(matrix.json()["nodes"]) <= {"w1", "w2", "w3"}


def test_an_unreadable_cluster_is_a_coded_error_not_scripted_results(use_cluster):
    from app.integrations.kubernetes.client import NotConnectedError

    use_cluster(FakeClusterSource(error=NotConnectedError("x")))

    response = client.get("/api/deployments/chaos/benchmarks", params={"namespace": "shop"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CLUSTER_NOT_CONNECTED"


def test_a_cluster_with_no_workloads_says_there_is_nothing_to_disrupt(use_cluster):
    use_cluster(FakeClusterSource(nodes=[node("w1"), node("w2")]))

    response = client.get("/api/deployments/chaos/benchmarks", params={"namespace": "empty"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TOPOLOGY_HAS_NO_WORKLOADS"


def test_an_unknown_source_is_a_client_error():
    assert client.get("/api/deployments/chaos/benchmarks", params={"source": "sample"}).status_code == 422
