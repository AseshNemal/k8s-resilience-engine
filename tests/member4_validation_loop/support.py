"""Shared builders for the validation-loop tests: a loop over a fake test cluster, with the pieces the tests vary."""

from datetime import datetime, timezone

from app.engine.recommendation_evaluation_validation.validation import predict_outcome
from app.engine.recommendation_evaluation_validation.validation_loop.grant import (
    GrantAuthority,
    GrantedCandidate,
    InMemoryKillBudget,
    new_grant,
)
from app.engine.recommendation_evaluation_validation.validation_loop.loop import LoopCandidate, LoopObserver, ValidationLoop
from app.engine.recommendation_evaluation_validation.validation_loop.models import Mode, resolve_mode
from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
from tests.support.member4_approvals import as_live
from tests.support.member4_harness import bookinfo_scenario
from tests.support.member4_test_cluster import FakeTestCluster

WORKERS = ["w1", "w2", "w3"]


class ProbingCluster(FakeTestCluster):
    """A fake test cluster whose workflow check says what the test scripts it to say."""

    def __init__(self, *args, probe_says=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.probe_says = probe_says

    def probe_reports(self):
        return {"afterChange": {"workflowSurvives": self.probe_says, "windows": {"failure": {"reasons": {}}}},
                "baselineFailure": {"workflowSurvives": self.probe_says, "windows": {"failure": {"reasons": {}}}}}


class Recorder(LoopObserver):
    def __init__(self):
        self.events = []

    def run_started(self, plan, candidates):
        self.events.append(("run", tuple(candidates)))

    def experiment_planned(self, key):
        self.events.append(("planned", key.worker, key.repeat))

    def stage(self, key, stage, detail=""):
        self.events.append(("stage", stage))

    def experiment_finished(self, outcome):
        self.events.append(("finished", outcome.key.worker, outcome.status.value))

    def candidate_finished(self, outcome):
        self.events.append(("candidate", outcome.candidate_id, outcome.status.value))


def setup(mode=Mode.QUICK, *, cluster_class=FakeTestCluster, cluster_options=None, max_failures=None, budget=None, **loop_options):
    scenario = bookinfo_scenario("concentrated")
    live = as_live(scenario.evaluate())
    recommendation_id = live.report.selected_recommendation_id
    proposal = build_yaml_proposal(live, recommendation_id)
    plan = resolve_mode(mode, WORKERS, riskiest_worker="w1")
    predicted = {w: predict_outcome(scenario.workflow, live, recommendation_id, failed_node=w) for w in WORKERS}
    candidate = LoopCandidate(recommendation_id, proposal.yaml, proposal.sha256, predicted, (1, 1))
    cluster = cluster_class(scenario.deployment, **(cluster_options or {}))
    grant = new_grant(run_id="run-1", plan_digest_value="p" * 64, target_name=cluster.target.name, cluster_uid="uid-1",
                      namespace="bookinfo", workers=plan.workers, candidates=(GrantedCandidate(recommendation_id, proposal.sha256),),
                      repeats=plan.repeats, measure_baseline=plan.measure_baseline,
                      approved_at=datetime(2026, 10, 24, tzinfo=timezone.utc))
    if max_failures is not None:
        grant = type(grant)(**{**grant.__dict__, "max_failures": max_failures})
    budget = budget if budget is not None else InMemoryKillBudget()
    armed = []
    options = dict(workflow=scenario.workflow, plan=plan, authority=GrantAuthority(grant, budget), candidates=[candidate],
                   executor_factory=lambda _candidate: cluster, arm=lambda auth, worker, digest: armed.append((worker, auth.action)))
    options.update(loop_options)
    return ValidationLoop(**options), cluster, budget, armed, candidate


def calls(cluster, prefix):
    return [c for c in cluster.calls if c.startswith(prefix)]


