"""The real gateway, injector, probe, executor and recorder, wired by ``compose``, over doubles at the lowest level.

The API server, ``docker`` and the application are in-memory fakes coupled the way the real ones are (see ``member4_fake_world``).
So this proves the glue between the pieces and the order and content of what they send; it proves nothing about a real cluster,
and the record it produces is stamped TEST_DOUBLE because everything underneath is.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.engine.recommendation_evaluation_validation.validation import (
    APPLY,
    FAIL_BASELINE,
    FAIL_NODE,
    DisposableClusterDesignation,
    RunState,
    ValidationPlan,
    ValidationRefusedError,
    ValidationRun,
    authorize,
)
from app.engine.recommendation_evaluation_validation.validation.experiment_record import ExperimentRecorder
from app.engine.recommendation_evaluation_validation.validation.experiment_repository import ExperimentRepository
from app.engine.recommendation_evaluation_validation.validation.workflow_probe import ProbeSpec
from app.integrations.kind.recovery import load_records, pending_records
from experiments.member4_real_validation.compose import StackConfig, WriteApproval, arm_stop_authorizations, build_stack
from tests.support.member4_approvals import approved_for_test
from tests.support.member4_fake_apiserver import KUBE_SYSTEM_UID, node_json
from tests.support.member4_fake_world import CONTEXT, SERVER, FakeWorld
from tests.support.member4_harness import (
    OFFLINE_NAMESPACE,
    Scenario,
    bookinfo_workflow,
    make_deployment,
    make_node,
    make_service,
    no_taints,
)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
DESIGNATION = DisposableClusterDesignation(name=CONTEXT, api_server=SERVER, cluster_uid=KUBE_SYSTEM_UID,
                                           namespace=OFFLINE_NAMESPACE, designated_by="asesh")
APPROVAL = WriteApproval(confirmed_by="asesh", cluster_uid=KUBE_SYSTEM_UID, statement="apply the reviewed patch to the test cluster")
PROBE = ProbeSpec(workflow_id="productpage-view", url="http://127.0.0.1:18080/productpage", required_text=("Book Details",),
                  forbidden_text=("currently unavailable",), min_samples=3)


def scenario_of(world):
    w1, w2, w3 = world.WORKERS
    nodes = [make_node(name) for name in world.WORKERS]
    services = [make_service("productpage", [w1, w2, w3]), make_service("details", [w2, w3]),
                make_service("reviews", [w1] * 3), make_service("ratings", [w1] * 2)]
    deployment = make_deployment(services, nodes)
    return Scenario("world", "", bookinfo_workflow(), deployment, no_taints(deployment), failed_node=w1)


def config(world, *, approval=APPROVAL, probe=PROBE):
    return StackConfig(kubeconfig_path=world.kubeconfig, designation=DESIGNATION, kind_cluster="resilience-validation",
                       workers=world.WORKERS, recovery_dir=world.recovery_dir, probe=probe, write_approval=approval)


def stack_of(world, **options):
    return build_stack(config(world, **options), runner=world.docker, transport=world.api.transport,
                       probe_transport=world.probe_transport, sleep=world.clock.sleep, clock=world.clock,
                       executor_options=dict(failure_window_s=20.0))


def run_experiment(session, world, stack, *, baseline=True, arm=2):
    approval, live = approved_for_test(scenario_of(world))
    plan = ValidationPlan.from_approval(approval, live, scenario_of(world).workflow, stack.executor.target)
    if arm:
        arm_stop_authorizations(stack, plan, "asesh", count=arm)
    run = ValidationRun(plan, stack.executor)
    repo = ExperimentRepository(session)
    recorder = ExperimentRecorder(repo, clock=lambda: NOW)
    experiment_id = recorder.start(plan, approval, stack.executor, run.thresholds)
    result = recorder.execute(run, experiment_id, authorize(plan, APPLY, "asesh"), authorize(plan, FAIL_NODE, "asesh"),
                              authorize(plan, FAIL_BASELINE, "asesh") if baseline else None)
    return plan, result, repo.get(experiment_id), repo


def test_a_full_experiment_with_a_measured_baseline_runs_through_the_real_components_and_restores_everything(session, tmp_path):
    world = FakeWorld(tmp_path)
    original = world.layout()
    stack = stack_of(world)

    plan, result, record, repo = run_experiment(session, world, stack)

    assert result.state is RunState.COMPLETED and result.validated is True and result.workloads_reverted is True
    # the baseline was measured, and the probe saw the difference the change made
    assert record.observed["baseline"]["basis"] == "MEASURED"
    probe = record.comparison["probe"]
    assert probe["baselineProbeSurvives"] is False and probe["probeSurvives"] is True and probe["observedImprovement"] is True
    assert probe["agreesWithReadiness"] is True and probe["matchesPrediction"] is True
    assert record.comparison["placement"]["match"] is True
    # everything was put back
    assert world.layout() == original and all(world.api.ready(n) for n in world.WORKERS)
    assert all(c["running"] for c in world.docker.containers.values())
    assert pending_records(world.recovery_dir) == [] and len(load_records(world.recovery_dir)) == 2


def test_what_was_sent_to_the_cluster_and_to_docker_is_exactly_what_the_design_allows(session, tmp_path):
    world = FakeWorld(tmp_path)
    stack = stack_of(world)

    run_experiment(session, world, stack)

    patches = world.api.patches()
    assert len(patches) == 2                                              # the change, and putting it back; nothing else
    (apply_path, apply_body, content_type), (restore_path, restore_body, _) = patches
    assert apply_path == restore_path == f"/apis/apps/v1/namespaces/{OFFLINE_NAMESPACE}/deployments/reviews"
    assert content_type == "application/strategic-merge-patch+json" and "resourceVersion" in apply_body["metadata"]
    assert restore_body["spec"]["template"]["spec"]["topologySpreadConstraints"] is None
    assert world.api.methods == {"GET", "PATCH"}
    commands = [call[1:] for call in world.docker.calls if call[1] != "inspect"]
    killed = f"{world.WORKERS[0]}"
    assert commands == [["kill", killed], ["start", killed], ["kill", killed], ["start", killed]]
    assert all(call[-1] != world.CONTROL_PLANE and call[-1] != "desktop-worker" for call in world.docker.calls if call[1] in ("kill", "start"))


def test_every_record_made_over_doubles_is_stamped_as_such_and_never_listed_as_a_measurement(session, tmp_path):
    world = FakeWorld(tmp_path)
    stack = stack_of(world)

    _, _, record, repo = run_experiment(session, world, stack)

    assert stack.executor.evidence_class == "TEST_DOUBLE" and record.evidence_class == "TEST_DOUBLE"
    assert stack.gateway.evidence_class == "TEST_DOUBLE" and stack.injector.evidence_class == "TEST_DOUBLE"
    assert repo.list_measurements() == []


def test_the_experiment_record_names_the_cluster_the_fault_method_and_the_probe(session, tmp_path):
    world = FakeWorld(tmp_path)

    _, _, record, _ = run_experiment(session, world, stack_of(world))

    assert record.cluster["cluster"]["kubeSystemUid"] == KUBE_SYSTEM_UID and record.cluster["namespace"] == OFFLINE_NAMESPACE
    assert "docker kill" in record.cluster["nodeFault"]["method"] and record.cluster["nodeFault"]["kind"] == "NODE_STOP"
    assert record.cluster["probe"]["url"] == "http://127.0.0.1:18080/productpage"
    assert record.cluster["baselineDeploymentVersions"]["reviews"] == "rv-reviews-1"


# --- the doors that stay shut ---------------------------------------------------------------------------------------------------

def test_without_a_write_approval_the_whole_stack_is_read_only_and_a_run_refuses_at_the_first_write(session, tmp_path):
    world = FakeWorld(tmp_path)
    stack = stack_of(world, approval=None)
    assert stack.writes_enabled is False and stack.gateway.allow_writes is False and stack.executor.writes_enabled is False

    with pytest.raises(ValidationRefusedError) as raised:
        run_experiment(session, world, stack)

    assert raised.value.code == "WRITES_DISABLED"
    assert world.api.methods == {"GET"} and not [c for c in world.docker.calls if c[1] in ("kill", "start")]


def test_a_write_approval_for_another_cluster_builds_nothing(tmp_path):
    world = FakeWorld(tmp_path)
    wrong = WriteApproval(confirmed_by="asesh", cluster_uid="not-this-cluster", statement="apply it")

    with pytest.raises(ValueError):
        stack_of(world, approval=wrong)
    with pytest.raises(ValueError):
        stack_of(world, approval=WriteApproval(confirmed_by=" ", cluster_uid=KUBE_SYSTEM_UID, statement="apply it"))

    assert world.api.requests == []      # refused before the cluster was contacted at all


def test_a_node_is_not_stopped_without_the_separate_authorization_even_with_every_other_approval(session, tmp_path):
    world = FakeWorld(tmp_path)
    stack = stack_of(world)

    plan, result, record, _ = run_experiment(session, world, stack, baseline=False, arm=0)

    assert result.state is RunState.FAILED and result.validated is False
    assert "NOT_ARMED" in result.reasons[0] and record.status == "FAILED" and record.validated is None
    assert not [c for c in world.docker.calls if c[1] == "kill"]
    assert result.workloads_reverted is True and len(world.api.patches()) == 2          # the change was put back
    assert world.layout() == world.api.original


def test_a_run_pointed_at_the_docker_desktop_cluster_never_gets_as_far_as_building(tmp_path):
    world = FakeWorld(tmp_path)
    world.api.nodes["desktop-worker"] = node_json("desktop-worker")      # a cluster that has Docker Desktop's node names

    with pytest.raises(ValidationRefusedError) as raised:
        stack_of(world)

    assert raised.value.code == "RESEARCH_CLUSTER_REFUSED" and world.api.methods == {"GET"}
