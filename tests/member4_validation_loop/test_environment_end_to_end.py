"""The real composition (gateway, executor, kind injector, probe, recovery) driven by the service, over doubles at the lowest level.

The API server, docker and the application are the in-memory fakes of ``member4_fake_world`` (coupled the way the real ones are:
a ``docker kill`` turns a node NotReady, a PATCH moves pods, the application answers while its mandatory services have a pod on a
node that is up). So this proves the glue and the order and content of what is sent; it proves nothing about a real cluster, and
every record is a TEST_DOUBLE.
"""

import pytest

from app.api.validation_loop_environment import ClusterConfig, ClusterProofEnvironment
from app.engine.recommendation_evaluation_validation.validation_loop.environment import DISPOSABLE, LoadedTarget, ProbeDefinition
from app.engine.recommendation_evaluation_validation.validation_loop.models import Mode
from app.engine.recommendation_evaluation_validation.validation_loop.service import (
    ApprovalInput,
    PlanRequest,
    ValidationLoopService,
)
from app.engine.recommendation_evaluation_validation.validation_loop.worker import LoopWorker
from app.integrations.kind.recovery import load_records, pending_records
from tests.member4_real_validation.test_stack_end_to_end import scenario_of
from tests.member4_validation_loop.conftest import NOW
from tests.support.member4_approvals import as_live
from tests.support.member4_fake_world import FakeWorld
from tests.support.member4_harness import OFFLINE_NAMESPACE

PROBE = ProbeDefinition("http://127.0.0.1:18080/productpage", 200, ("Book Details",), ("currently unavailable",))


def environment(world, **options):
    scenario = scenario_of(world)

    def loader(workflow_id, namespace, gateway):
        return LoadedTarget(scenario.workflow, "FROZEN", as_live(scenario.evaluate()), gateway.read_graph(namespace))

    config = ClusterConfig(DISPOSABLE, "resilience-validation", "resilience-validation", world.kubeconfig, False, world.recovery_dir)
    return ClusterProofEnvironment(config, loader=loader, runner=world.docker, transport=world.api.transport,
                                   probe_transport=world.probe_transport, sleep=world.clock.sleep, clock=world.clock,
                                   executor_options=dict(failure_window_s=20.0), **options)


def service(repo, world, **options):
    return ValidationLoopService(environment(world, **options), repo, LoopWorker(repo), clock=lambda: NOW)


def request(mode=Mode.QUICK, probe=PROBE):
    return PlanRequest(mode=mode, workflow_id="productpage-view", namespace=OFFLINE_NAMESPACE, probe=probe)


def run_to_end(svc, req, **approval):
    plan = svc.plan(req)
    assert plan.can_approve, [i.to_dict() for i in plan.issues]
    started = svc.approve(req, ApprovalInput(plan.digest, **approval))
    svc._worker.join(30)
    return svc.view(started["runKey"], research=True)


def test_the_plan_reads_the_real_gateway_and_lists_the_worker_containers_it_would_stop(repo, tmp_path):
    world = FakeWorld(tmp_path)

    plan = service(repo, world).plan(request())

    assert plan.identity.cluster_uid and plan.mode_plan.workers == tuple(sorted(world.WORKERS))
    assert plan.readiness is not None and plan.readiness.identifiable and plan.readiness.controllable
    assert not any(command[1] in ("kill", "start") for command in world.docker.calls)         # planning stops nothing


def test_a_quick_run_proves_the_change_stopping_each_worker_once_and_restores_everything(repo, tmp_path):
    world = FakeWorld(tmp_path)
    original = world.layout()

    view = run_to_end(service(repo, world), request())

    assert view["status"] == "COMPLETED", (view["status"], view["stopReason"])
    assert view["result"]["winner"] is not None and view["metrics"]["finished"] == 3
    kills = [c[-1] for c in world.docker.calls if c[1] == "kill"]
    starts = [c[-1] for c in world.docker.calls if c[1] == "start"]
    assert kills == sorted(world.WORKERS) and starts == kills                                 # each stopped once, each started again
    assert world.layout() == original                                                         # the Deployments are back
    assert all(world.api.ready(w) for w in world.WORKERS) and pending_records(world.recovery_dir) == []
    assert view["safety"]["workersRestored"] == "confirmed" and view["safety"]["deploymentsRestored"] == "confirmed"


def test_the_probe_and_readiness_are_both_recorded_for_every_experiment(repo, tmp_path):
    world = FakeWorld(tmp_path)

    view = run_to_end(service(repo, world), request())

    for stored in view["research"]["experiments"]:
        outcome = stored["outcome"]
        assert outcome["evidenceLevel"] == "L2_PROBE" and outcome["readiness"]["holds"] is True
        assert outcome["probe"]["windows"]["failure"]["samples"] >= 1 and outcome["probe"]["workflowSurvives"] is True
        assert outcome["recovery"]["verified"] is True


def test_a_candidate_the_scheduler_does_not_honour_fails_the_run_and_the_cluster_is_still_put_back(repo, tmp_path):
    world = FakeWorld(tmp_path, spreads=False)
    original = world.layout()

    view = run_to_end(service(repo, world), request())

    assert view["result"]["winner"] is None and view["result"]["candidates"][0]["status"] == "FAILED"
    assert [c[-1] for c in world.docker.calls if c[1] == "kill"] == [sorted(world.WORKERS)[0]]       # stopped at the first conclusive failure
    assert world.layout() == original and pending_records(world.recovery_dir) == []


def test_every_kill_is_armed_one_at_a_time_and_recorded_before_it_happens(repo, tmp_path):
    world = FakeWorld(tmp_path)

    run_to_end(service(repo, world), request(Mode.SMOKE))

    records = load_records(world.recovery_dir)
    assert len(records) == 1 and records[0]["state"] == "RESTORED"
    assert records[0]["authorizedBy"].startswith("run-approval:") and records[0]["planDigest"]


def test_docker_desktop_style_containers_are_never_touched_by_a_disposable_run(repo, tmp_path):
    world = FakeWorld(tmp_path)

    run_to_end(service(repo, world), request())

    touched = {c[-1] for c in world.docker.calls if c[1] in ("kill", "start")}
    assert touched <= set(world.WORKERS) and "desktop-worker" not in touched and world.CONTROL_PLANE not in touched


def test_a_run_interrupted_by_a_restart_is_recovered_with_the_real_recoverer_and_kills_nothing(repo, tmp_path):
    world = FakeWorld(tmp_path)
    svc = service(repo, world)
    started = svc.approve(request(Mode.SMOKE), ApprovalInput(svc.plan(request(Mode.SMOKE)).digest))
    svc._worker.join(30)
    # Make it look like the backend died while a worker was down: the container is stopped, its record is pending, the run is live.
    from app.integrations.kind.node_fault import KindWorkerFaultInjector
    from app.integrations.kind.recovery import save_record
    victim = sorted(world.WORKERS)[0]
    world.docker.containers[victim]["running"] = False
    world._container_changed(victim, False)
    save_record(world.recovery_dir, {"schema": 1, "recordId": "left-behind", "cluster": "resilience-validation", "container": victim,
                                     "node": victim, "authorizedBy": "x", "planDigest": "y", "state": "STOPPED", "createdAt": "t",
                                     "stoppedAt": "t", "restoredAt": None, "lastError": None, "method": "docker kill",
                                     "restoreCommand": ["docker", "start", victim]})
    from app.engine.recommendation_evaluation_validation.validation_loop.models import RunStatus
    repo.set_status(started["runKey"], RunStatus.RUNNING)
    kills_before = len([c for c in world.docker.calls if c[1] == "kill"])

    fresh = service(repo, world)
    fresh.ensure_recovered()

    run = repo.get_run(started["runKey"])
    assert run["status"] == "INTERRUPTED" and run["recovery"]["complete"] is True
    assert world.docker.containers[victim]["running"] is True and pending_records(world.recovery_dir) == []
    assert len([c for c in world.docker.calls if c[1] == "kill"]) == kills_before            # recovery killed nothing
