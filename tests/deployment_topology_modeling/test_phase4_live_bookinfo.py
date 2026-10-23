"""Live experiments on the Bookinfo workload: the right objects are disrupted, without a cluster.

Bookinfo names its Deployment reviews-v1 for Service reviews, so the scenarios must find the target's
pods by the Service name and scale/restart the Deployment name. kubectl is the recording fake of
test_phase4_live_runner; observations are real Component 2 analyses of hand-written placements.
"""
from pathlib import Path

import pytest
import yaml

from experiments.placement_drift.live import bookinfo_workloads
from experiments.placement_drift.live import scenarios as sc
from experiments.placement_drift.live.runner import LiveExperimentRunner
from experiments.placement_drift.live.workloads import BOOKINFO, STANDIN, WORKLOADS
from tests.deployment_topology_modeling.test_phase4_live_runner import FakeKube, analysis


def bookinfo_runner(tmp_path, kube, observations, observe_nodes=lambda: []):
    seq = iter(observations)
    return LiveExperimentRunner(kube, BOOKINFO.configs, list(BOOKINFO.deployments), tmp_path,
                                observe=lambda ns, manifest: next(seq), observe_nodes=observe_nodes,
                                settle_s=0, workload=BOOKINFO)


SPREAD = {"reviews": ["n1", "n2", "n3"], "details": ["n1", "n2", "n3"]}
PACKED = {"reviews": ["n1", "n1", "n1"], "details": ["n1", "n2", "n3"]}


def test_the_workloads_name_their_target_service_and_deployment():
    assert WORKLOADS == {"standin": STANDIN, "bookinfo": BOOKINFO}
    assert (STANDIN.target_service, STANDIN.target_deployment) == ("paymentservice", "paymentservice")
    assert (BOOKINFO.target_service, BOOKINFO.target_deployment) == ("reviews", "reviews-v1")
    assert BOOKINFO.target_selector == "app=reviews"
    assert BOOKINFO.namespace == "bookinfo-exp"
    assert set(BOOKINFO.deployments) == {"details-v1", "ratings-v1", "reviews-v1", "productpage-v1"}
    for path in BOOKINFO.configs.values():
        assert Path(path).is_file(), path


@pytest.mark.parametrize("scenario, replicas", [("02_scale_up", 6), ("03_scale_down", 2)])
def test_scaling_scales_the_target_deployment(tmp_path, scenario, replicas):
    kube = FakeKube()
    record = bookinfo_runner(tmp_path, kube, [analysis(SPREAD), analysis(SPREAD)]).run_once(
        sc.SCENARIOS_BY_ID[scenario], "A-no-rule", 1)
    assert ("scale", ("reviews-v1", replicas)) in kube.calls
    assert record.action["action"] == f"kubectl scale deployment/reviews-v1 --replicas={replicas}"


def test_rolling_update_restarts_the_target_deployment(tmp_path):
    kube = FakeKube()
    bookinfo_runner(tmp_path, kube, [analysis(SPREAD), analysis(SPREAD)]).run_once(
        sc.SCENARIOS_BY_ID["04_rolling_update"], "A-no-rule", 1)
    assert ("rollout_restart", ("reviews-v1",)) in kube.calls


def test_pod_deletion_finds_the_target_pods_by_service_name(tmp_path):
    kube = FakeKube()
    t0 = analysis({"reviews": ["n1", "n1", "n2"], "details": ["n2"]})
    bookinfo_runner(tmp_path, kube, [t0, t0]).run_once(sc.SCENARIOS_BY_ID["05_pod_deletion"], "A-no-rule", 1)
    assert ("delete_pod", ("reviews-2",)) in kube.calls  # the only reviews pod on n2


def test_cordon_restarts_the_target_deployment(tmp_path):
    kube = FakeKube()
    t0 = analysis({"reviews": ["n2", "n2", "n3"], "details": ["n1"]})
    bookinfo_runner(tmp_path, kube, [t0, t0]).run_once(sc.SCENARIOS_BY_ID["06_node_cordon"], "A-no-rule", 1)
    assert ("cordon", ("n2",)) in kube.calls
    assert ("rollout_restart", ("reviews-v1",)) in kube.calls


def test_constrained_resources_waits_for_the_target_pods_and_scales_it_back(tmp_path):
    kube = FakeKube()

    class Node:
        def __init__(self, name, cpu):
            self.name, self.cpu_available = name, cpu

    nodes = lambda: [Node("n1", "4.0"), Node("n2", "2.0")]  # noqa: E731
    bookinfo_runner(tmp_path, kube, [analysis(SPREAD), analysis(PACKED)], observe_nodes=nodes).run_once(
        sc.SCENARIOS_BY_ID["08_constrained_resources"], "A-no-rule", 1)
    assert ("scale", ("reviews-v1", 0)) in kube.calls
    assert ("wait_until_no_pods", ("app=reviews",)) in kube.calls
    assert ("scale", ("reviews-v1", 3)) in kube.calls


def test_the_record_measures_and_names_the_bookinfo_target(tmp_path):
    record = bookinfo_runner(tmp_path, FakeKube(), [analysis(SPREAD), analysis(PACKED)]).run_once(
        sc.SCENARIOS_BY_ID["01_initial_deployment"], "A-no-rule", 1)
    assert (record.target_status_t0, record.target_status_t1) == ("NOT_EXPOSED", "EXPOSED")
    assert (record.workload, record.target_service) == ("bookinfo", "reviews")
    assert record.changed_services == ["reviews"]


def test_standin_runs_are_unchanged(tmp_path):
    seq = iter([analysis({"paymentservice": ["n1", "n2", "n3"]})] * 2)
    kube = FakeKube()
    runner = LiveExperimentRunner(kube, {"A": "a.yaml"}, ["paymentservice"], tmp_path,
                                  observe=lambda ns, manifest: next(seq), observe_nodes=lambda: [], settle_s=0)
    record = runner.run_once(sc.SCENARIOS_BY_ID["02_scale_up"], "A", 1)
    assert ("scale", ("paymentservice", 6)) in kube.calls
    assert (record.workload, record.target_service) == ("standin", "paymentservice")


@pytest.mark.parametrize("config", ["config-a-no-rule", "config-b-topology-spread"])
def test_generated_bookinfo_configs_stay_in_their_own_namespace(config):
    docs = list(yaml.safe_load_all((bookinfo_workloads.OUT_DIR / f"{config}.yaml").read_text(encoding="utf-8")))
    assert {d["metadata"].get("namespace", d["metadata"]["name"]) for d in docs} == {"bookinfo-exp"}
    deployments = [d for d in docs if d["kind"] == "Deployment"]
    assert {d["metadata"]["name"] for d in deployments} == set(BOOKINFO.deployments)
    for d in deployments:
        assert d["spec"]["replicas"] == 3
        pod = d["spec"]["template"]
        assert pod["metadata"]["labels"]["experiment"] == "placement-drift"
        container = pod["spec"]["containers"][0]
        assert container["resources"] == {"requests": {"cpu": "100m", "memory": "128Mi"}}
        assert container["image"].endswith(":1.20.3")
        has_spread = "topologySpreadConstraints" in pod["spec"]
        assert has_spread is (config == "config-b-topology-spread")


def test_generated_configs_match_the_generator():
    for config, spread in (("config-a-no-rule", False), ("config-b-topology-spread", True)):
        on_disk = list(yaml.safe_load_all((bookinfo_workloads.OUT_DIR / f"{config}.yaml").read_text(encoding="utf-8")))
        assert on_disk == bookinfo_workloads.documents(spread), config
