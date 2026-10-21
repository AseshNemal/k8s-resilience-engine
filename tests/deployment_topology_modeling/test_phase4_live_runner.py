"""Live experiment runner: flow, safety and metrics, without a cluster.

Observations are real Component 2 analyses built from hand-written placements; kubectl is a fake
that records commands.
"""
import json
import time
from unittest.mock import patch

import pytest

from app.engine.deployment_topology_modeling import service
from app.engine.deployment_topology_modeling.live_state.cluster_models import ClusterNode
from app.engine.deployment_topology_modeling.live_state.concentration_analyzer import analyze_replica_concentration
from experiments.placement_drift.exceptions import ChaosSafetyError, ScenarioExecutionError
from experiments.placement_drift.live import scenarios as sc
from experiments.placement_drift.live.kubectl import Kubectl
from experiments.placement_drift.live.runner import LiveExperimentRunner
from experiments.placement_drift.live.summary import describe, summarise

NODES = ["n1", "n2", "n3"]


def analysis(placement: dict[str, list[str]]):
    """placement: service -> list of nodes (one ready pod per entry)."""
    nodes = [ClusterNode(name=n, zone=None, cpu_available="1.0", memory_available="1e9",
                         labels={"kubernetes.io/hostname": n}, ready=True) for n in NODES]
    workloads = {
        svc: {"name": svc, "namespace": "exp", "declaredReplicas": len(ns), "deploymentNames": [svc],
              "pods": [{"name": f"{svc}-{i}", "node": n, "ready": True} for i, n in enumerate(ns)]}
        for svc, ns in placement.items()
    }
    analyzed = analyze_replica_concentration(workloads)
    return service._analyse(nodes, analyzed, None, "test", time.perf_counter(), load_manifests=False)


class FakeKube:
    def __init__(self, fail_wait_after_disruption=False):
        self.context, self.namespace = "kind-test", "exp"
        self.calls = []
        self.fail_wait_after_disruption = fail_wait_after_disruption
        self.disrupted = False

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args))
        return record

    def worker_nodes(self):
        return NODES

    def wait_until_stable(self, deployments, timeout_s=180, poll_s=2.0):
        self.calls.append(("wait_until_stable", tuple(deployments)))
        if self.disrupted and self.fail_wait_after_disruption:
            raise ScenarioExecutionError("Pending pods")
        return 1.5

    def delete_pod(self, pod):
        self.disrupted = True
        self.calls.append(("delete_pod", (pod,)))


def make_runner(tmp_path, kube, observations):
    seq = iter(observations)
    return LiveExperimentRunner(kube, {"A": "a.yaml"}, ["paymentservice", "cartservice"], tmp_path,
                                observe=lambda ns, manifest: next(seq), observe_nodes=lambda: [], settle_s=0)


T0 = {"paymentservice": ["n1", "n2", "n3"], "cartservice": ["n1", "n1", "n1"]}
T1 = {"paymentservice": ["n1", "n1", "n1"], "cartservice": ["n1", "n1", "n1"]}


def test_run_once_flow_and_hand_computed_metrics(tmp_path):
    kube = FakeKube()
    record = make_runner(tmp_path, kube, [analysis(T0), analysis(T1)]).run_once(
        sc.SCENARIOS_BY_ID["05_pod_deletion"], "A", 1)

    names = [c[0] for c in kube.calls]
    # reset (uncordon all, delete, wait gone, apply, wait ready) happens before the disruption
    assert names[:3] == ["uncordon", "uncordon", "uncordon"]
    assert names.index("delete_experiment_workloads") < names.index("apply") < names.index("delete_pod")
    # payment:  NOT_EXPOSED -> EXPOSED ; cart: EXPOSED -> EXPOSED  => D_rate 1/2
    assert record.d_rate == pytest.approx(0.5)
    assert record.changed_services == ["paymentservice"]
    # exposure rate 1/2 -> 2/2
    assert record.exposure_rate_t0 == pytest.approx(0.5)
    assert record.exposure_rate_t1 == pytest.approx(1.0)
    assert record.delta_e_rate == pytest.approx(0.5)
    assert (record.target_status_t0, record.target_status_t1) == ("NOT_EXPOSED", "EXPOSED")
    assert record.stable_after_disruption is True


def test_pod_deletion_picks_pod_on_least_used_node(tmp_path):
    kube = FakeKube()
    t0 = analysis({"paymentservice": ["n1", "n1", "n2"], "cartservice": ["n1"]})
    make_runner(tmp_path, kube, [t0, t0]).run_once(sc.SCENARIOS_BY_ID["05_pod_deletion"], "A", 1)
    assert ("delete_pod", ("paymentservice-2",)) in kube.calls  # the only pod on n2


def test_cordon_targets_busiest_node_and_is_undone(tmp_path):
    kube = FakeKube()
    t0 = analysis({"paymentservice": ["n2", "n2", "n3"], "cartservice": ["n1"]})
    make_runner(tmp_path, kube, [t0, t0]).run_once(sc.SCENARIOS_BY_ID["06_node_cordon"], "A", 1)
    names = [c for c in kube.calls]
    assert ("cordon", ("n2",)) in names
    assert ("rollout_restart", ("paymentservice",)) in names
    assert names[-1] == ("uncordon", ("n2",))


def test_drain_targets_node_with_most_experiment_pods(tmp_path):
    kube = FakeKube()
    t0 = analysis({"paymentservice": ["n1", "n2", "n3"], "cartservice": ["n3", "n3"]})
    make_runner(tmp_path, kube, [t0, t0]).run_once(sc.SCENARIOS_BY_ID["07_node_drain"], "A", 1)
    assert ("drain", ("n3",)) in kube.calls
    assert kube.calls[-1] == ("uncordon", ("n3",))


def test_unsettled_workload_is_recorded_not_crashed(tmp_path):
    kube = FakeKube(fail_wait_after_disruption=True)
    record = make_runner(tmp_path, kube, [analysis(T0), analysis(T0)]).run_once(
        sc.SCENARIOS_BY_ID["05_pod_deletion"], "A", 1)
    assert record.stable_after_disruption is False
    assert "Pending" in record.error
    assert record.d_rate == pytest.approx(0.0)


def test_failed_run_is_written_and_batch_continues(tmp_path):
    def boom(ctx):
        raise RuntimeError("kubectl exploded")

    broken = sc.LiveScenario("99_broken", "broken", sc.DisruptionType.POD_DELETION_CHAOS, "", boom)
    kube = FakeKube()
    runner = make_runner(tmp_path, kube, [analysis(T0), analysis(T0), analysis(T1)])
    records = runner.run([broken, sc.SCENARIOS_BY_ID["01_initial_deployment"]], ["A"], 1)
    assert len(records) == 1
    failed = json.loads((tmp_path / "99_broken__A__run1.json").read_text())
    assert failed["failed"] is True
    assert "exploded" in failed["error"]


def test_summary_statistics_hand_computed(tmp_path):
    # [0, 0.5, 0.5]: mean 1/3, s = 0.2887, t(2) = 4.303 -> half-width 0.7172 -> CI clipped [0, 1]
    stat = describe([0.0, 0.5, 0.5])
    assert stat["mean"] == pytest.approx(0.3333, abs=1e-4)
    assert stat["sd"] == pytest.approx(0.2887, abs=1e-4)
    assert stat["ci95"] == pytest.approx([0.0, 1.0], abs=1e-4)
    assert describe([0.4])["ci95"] is None
    assert describe([])["mean"] is None
    # milliseconds are not a share: [400, 500] -> mean 450, s = 70.71, t(1) = 12.706 -> +-635.3, not clipped
    ms = describe([400.0, 500.0], rate=False)
    assert ms["ci95"] == pytest.approx([-185.3, 1085.3], abs=0.1)

    for rep, d in enumerate([0.0, 0.5, 0.5], start=1):
        (tmp_path / f"05_pod_deletion__A__run{rep}.json").write_text(json.dumps({
            "scenario_id": "05_pod_deletion", "config": "A", "stable_after_disruption": True,
            "d_rate": d, "exposure_rate_t0": 0.5, "exposure_rate_t1": 0.5, "compliance_rate_t1": None,
            "target_status_t0": "NOT_EXPOSED", "target_status_t1": "EXPOSED" if d else "NOT_EXPOSED",
            "observation_ms_t0": 10.0, "observation_ms_t1": 12.0,
        }))
    (cond,) = summarise(tmp_path)["conditions"]
    assert cond["runs"] == 3
    assert cond["failed_runs"] == 0
    assert cond["target_became_exposed"] == 2
    assert (tmp_path / "summary.md").exists()


def test_kubectl_safety_rules():
    with pytest.raises(ChaosSafetyError):
        Kubectl("docker-desktop", "exp")
    with pytest.raises(ChaosSafetyError):
        Kubectl("kind-x", "kube-system")
    kube = Kubectl("kind-x", "exp")
    with patch.object(Kubectl, "worker_nodes", return_value=["w1"]):
        with pytest.raises(ChaosSafetyError):
            kube.drain("x-control-plane")


def test_every_kubectl_command_is_pinned_to_the_context():
    kube = Kubectl("kind-research", "exp")
    with patch("experiments.placement_drift.live.kubectl.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        kube.scale("paymentservice", 6)
        kube.uncordon("w1")
    scale_cmd, uncordon_cmd = (c.args[0] for c in run.call_args_list)
    assert scale_cmd[:5] == ["kubectl", "--context", "kind-research", "--namespace", "exp"]
    assert uncordon_cmd[:3] == ["kubectl", "--context", "kind-research"]
    assert "--namespace" not in uncordon_cmd  # node commands are cluster-scoped


def test_pinned_kubeconfig_holds_only_the_experiment_context():
    kube = Kubectl("kind-research", "exp")
    with patch("experiments.placement_drift.live.kubectl.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "current-context: kind-research\n"
        path = kube.write_pinned_kubeconfig()
    try:
        cmd = run.call_args.args[0]
        assert cmd == ["kubectl", "--context", "kind-research", "config", "view", "--minify", "--flatten"]
        assert path.read_text(encoding="utf-8") == "current-context: kind-research\n"
    finally:
        path.unlink()


def test_runner_points_the_collector_at_the_experiment_cluster(tmp_path, monkeypatch):
    # The collector reads the kubeconfig's current context, so the runner must hand it a kubeconfig that
    # holds only the experiment context; otherwise T0/T1 would be observed on the user's current cluster.
    pinned = tmp_path / "pinned.yaml"
    pinned.write_text("current-context: kind-test\n", encoding="utf-8")
    kube = FakeKube()
    kube.write_pinned_kubeconfig = lambda: pinned
    monkeypatch.setenv("KUBECONFIG", "user-kubeconfig")

    LiveExperimentRunner(kube, {"A": "a.yaml"}, ["paymentservice"], tmp_path)

    assert service.os.environ["KUBECONFIG"] == str(pinned)
