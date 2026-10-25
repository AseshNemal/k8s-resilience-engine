"""The fault-injection validation loop: prove candidate placement changes on a test cluster, one worker failure at a time.

For each candidate (best first, as many as the mode asks for) the loop applies the change ONCE, then stops each authorized worker
in turn and checks the workflow, verifies the worker came back after every failure, and finally puts the candidate's Deployments
back. A candidate that conclusively fails (or whose evidence cannot be completed) ends early; what was not run is recorded as
SKIPPED with the reason, never silently dropped. Nothing is selected unless every planned experiment of a candidate passed.

What it does NOT do. It does not decide what may be touched: the executor and the fault injector it is given keep every
existing safety check (the cluster must be the designated one, only a genuine node stop is accepted, writes must be armed). It
does not shorten any controller grace period, skip the recovery check, or stand in for a node failure with a pod deletion: the
failure is whatever ``executor.fail_node`` really does. It does not repeat a node failure on its own after a crash; recovering
an interrupted run (restoring workers, reverting workloads) is a separate step in ``worker.py`` and never kills anything.

Authorization. Every step is authorized by a ``GrantAuthority`` minted from the one run approval, inside its bounds; every node
failure is counted against the durable kill budget BEFORE the injector is armed.

Evidence. Readiness and the HTTP probe are judged separately (``verdict.py``). The prediction for each worker comes from Component
3 through ``predict_outcome``; it is compared with the readiness-derived observation, and the original is never rewritten.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.recommendation_evaluation_validation.validation.comparison import compare_outcomes
from app.engine.recommendation_evaluation_validation.validation.models import (
    DERIVED,
    MEASURED,
    Authorization,
    ObservedOutcome,
    PredictedOutcome,
    ValidationInterrupted,
    ValidationRefusedError,
    ValidationThresholds,
)
from app.engine.recommendation_evaluation_validation.validation.run import ValidationExecutor
from app.engine.recommendation_evaluation_validation.validation_loop.grant import GrantAuthority
from app.engine.recommendation_evaluation_validation.validation_loop.models import (
    BASELINE,
    CANDIDATE,
    CandidateOutcome,
    CandidateStatus,
    ExperimentKey,
    ExperimentOutcome,
    ExperimentStatus,
    ModePlan,
    RunStatus,
)
from app.engine.recommendation_evaluation_validation.validation_loop.verdict import (
    judge_candidate,
    judge_experiment,
    mandatory_readiness,
    select_winner,
)
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.workflow import WorkflowGraph

# The stages the UI shows, in the order they happen for one experiment.
STAGES = ("PREPARE", "APPLY_CANDIDATE", "VERIFY_ROLLOUT", "INJECT_FAILURE", "PROBE", "RECOVER_WORKER", "RESTORE_DEPLOYMENT")


class KillBlocked(Exception):
    """A check before a kill refused it (an essential workload would go down, evidence is incomplete, health is degraded)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


@dataclass(frozen=True)
class LoopCandidate:
    """One candidate change: the YAML to apply once and the prediction for each worker that will be stopped."""

    candidate_id: str
    yaml_text: str
    yaml_sha256: str
    predicted: dict[str, PredictedOutcome]      # worker -> prediction, fixed before anything is applied
    change_key: tuple = ()                      # smaller sorts first (added replicas, Deployments changed, cost, rank)


class LoopObserver:
    """Receives what the loop is doing, in order. The database-backed one persists it; the default does nothing."""

    def run_started(self, plan: ModePlan, candidates: list[str]) -> None: ...
    def experiment_planned(self, key: ExperimentKey) -> None: ...
    def stage(self, key: ExperimentKey | None, stage: str, detail: str = "") -> None: ...
    def experiment_finished(self, outcome: ExperimentOutcome) -> None: ...
    def candidate_finished(self, outcome: CandidateOutcome) -> None: ...


class ExecutorFactory(Protocol):
    def __call__(self, candidate_id: str | None) -> ValidationExecutor:
        """A fresh executor for a candidate (``None``: the unchanged deployment, for the baseline). Refuses an unhealthy cluster."""


@dataclass
class LoopResult:
    status: RunStatus
    candidates: list[CandidateOutcome] = field(default_factory=list)
    baseline: list[ExperimentOutcome] = field(default_factory=list)
    winner: str | None = None
    kills_used: int = 0
    stop_reason: str | None = None
    needs_manual_restore: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "winner": self.winner, "killsUsed": self.kills_used,
                "stopReason": self.stop_reason, "needsManualRestore": self.needs_manual_restore,
                "baseline": [e.to_dict() for e in self.baseline], "candidates": [c.to_dict() for c in self.candidates]}


class _RunStop(Exception):
    """Ends the whole run (abort, exhausted budget, a node that could not be restored)."""

    def __init__(self, status: RunStatus, reason: str) -> None:
        super().__init__(reason)
        self.status, self.reason = status, reason


def observed_estimate(durations_s: list[float], remaining: int) -> dict[str, Any] | None:
    """A time estimate from experiments that ACTUALLY ran (this run or past ones); None when there is nothing observed.

    It is a mean, with the sample count and the range, so the page can say what it rests on. Nothing is promised.
    """
    if not durations_s or remaining < 0:
        return None
    mean = sum(durations_s) / len(durations_s)
    return {"remainingExperiments": remaining, "estimatedSeconds": round(mean * remaining, 1), "basedOnExperiments": len(durations_s),
            "meanExperimentSeconds": round(mean, 1), "minExperimentSeconds": round(min(durations_s), 1),
            "maxExperimentSeconds": round(max(durations_s), 1)}


class ValidationLoop:
    def __init__(self, *, workflow: WorkflowGraph, plan: ModePlan, authority: GrantAuthority, candidates: list[LoopCandidate],
                 executor_factory: ExecutorFactory, probe_required: bool = False,
                 thresholds: ValidationThresholds | None = None, arm: Callable[[Authorization, str, str], None] | None = None,
                 before_kill: Callable[[str, str | None], None] | None = None, node_ready: Callable[[str], bool | None] | None = None,
                 abort: Callable[[], bool] = lambda: False, observer: LoopObserver | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.workflow, self.plan, self.authority, self.candidates = workflow, plan, authority, list(candidates)[:plan.candidates]
        self._factory, self._probe_required = executor_factory, probe_required
        self._thresholds = thresholds or ValidationThresholds()
        self._arm, self._before_kill, self._node_ready = arm, before_kill, node_ready
        self._abort, self._observer, self._clock = abort, observer or LoopObserver(), clock
        self._manual_restore = False
        self._finished: list[CandidateOutcome] = []
        self._baseline_outcomes: list[ExperimentOutcome] = []
        self._baselines: dict[str, ObservedOutcome] = {}
        self._events: list[dict[str, Any]] = []

    # -- helpers -------------------------------------------------------------------------------------------------

    def _log(self, event: str, detail: str = "") -> None:
        self._events.append({"event": event, "detail": detail})

    def _check_abort(self) -> None:
        if self._abort():
            raise _RunStop(RunStatus.INTERRUPTED, "Abort was requested; stopped at a safe point.")

    def planned_keys(self, candidate: LoopCandidate) -> list[ExperimentKey]:
        return [ExperimentKey(CANDIDATE, candidate.candidate_id, worker, repeat)
                for repeat in range(1, self.plan.repeats + 1) for worker in self.plan.workers]

    def _skip(self, key: ExperimentKey, reason: str) -> ExperimentOutcome:
        outcome = ExperimentOutcome(key=key, status=ExperimentStatus.SKIPPED, reasons=[reason])
        self._observer.experiment_finished(outcome)
        return outcome

    def _inject(self, executor: ValidationExecutor, key: ExperimentKey, worker: str, *, baseline: bool) -> tuple[DeploymentGraph | None, dict | None, list[str], dict]:
        """One authorized node failure with its recovery. Returns (graph after failure, probe report, problems, recovery)."""
        try:
            step, stop = self.authority.authorize_failure(executor.target, worker, baseline=baseline)
        except ValidationRefusedError as error:
            # Outside the approved bounds (a worker, a target, the kill budget): the whole run stops, nothing is armed.
            raise _RunStop(RunStatus.INTERRUPTED, f"{error.code}: {error.message}") from error
        if self._arm is not None:
            self._arm(stop, worker, step.plan_digest)
        problems: list[str] = []
        graph: DeploymentGraph | None = None
        self._observer.stage(key, "INJECT_FAILURE", f"stopping {worker}")
        try:
            graph = executor.fail_node(worker)
        except ValidationRefusedError as error:
            problems.append(f"{error.code}: {error.message}")
        except ValidationInterrupted as error:
            problems.append(f"interrupted: {error}")
        except Exception as error:      # noqa: BLE001  (the failure may not have been produced; say so, then restore regardless)
            problems.append(f"the failure was not produced or observed: {error}")
        finally:
            recovery = self._recover(executor, key, worker)
        probe = None
        reports = getattr(executor, "probe_reports", None)
        if callable(reports):
            probe = (reports() or {}).get("baselineFailure" if baseline else "afterChange")
        return graph, probe, problems, recovery

    def _recover(self, executor: ValidationExecutor, key: ExperimentKey, worker: str) -> dict[str, Any]:
        """Bring the worker back and VERIFY it. A restore that fails or is not confirmed is flagged, never swallowed."""
        self._observer.stage(key, "RECOVER_WORKER", f"starting {worker} and waiting for Ready")
        recovery: dict[str, Any] = {"worker": worker, "restored": False, "verified": False}
        try:
            executor.restore_node(worker)
            recovery["restored"] = True
        except Exception as error:      # noqa: BLE001
            recovery["error"] = str(error)
        if recovery["restored"]:
            ready = self._node_ready(worker) if self._node_ready is not None else None
            recovery["verified"] = ready is True or ready is None      # None: the executor itself waited for Ready
            recovery["independentCheck"] = ready
            if ready is False:
                recovery["error"] = f"{worker} was started but does not report Ready"
        if not recovery["restored"] or recovery.get("independentCheck") is False:
            self._manual_restore = True
        return recovery

    # -- experiments ---------------------------------------------------------------------------------------------

    def _run_baselines(self) -> list[ExperimentOutcome]:
        outcomes = self._baseline_outcomes
        if not self.plan.measure_baseline:
            return outcomes
        executor = self._factory(None)
        for worker in self.plan.workers:
            self._check_abort()
            key = ExperimentKey(BASELINE, None, worker, 1)
            self._observer.experiment_planned(key)
            started = self._clock()
            blocked = self._blocked(worker, None)
            if blocked:
                outcomes.append(self._skip(key, blocked))
                raise _RunStop(RunStatus.INTERRUPTED, blocked)
            graph, probe, problems, recovery = self._inject(executor, key, worker, baseline=True)
            if self._manual_restore:
                outcome = ExperimentOutcome(key=key, status=ExperimentStatus.INCONCLUSIVE, recovery=recovery,
                                            reasons=problems + [f"{worker} could not be restored."])
                self._observer.experiment_finished(outcome)
                outcomes.append(outcome)
                raise _RunStop(RunStatus.NEEDS_MANUAL_RESTORE, f"{worker} could not be restored.")
            if graph is None:
                outcome = ExperimentOutcome(key=key, status=ExperimentStatus.INCONCLUSIVE, reasons=problems, recovery=recovery)
            else:
                readiness = mandatory_readiness(graph, self.workflow.mandatory_services, worker)
                self._baselines[worker] = ObservedOutcome(predict_failure_impact(self.workflow, graph, worker), MEASURED)
                outcome = judge_experiment(key, readiness, probe, probe_required=self._probe_required)
                outcome.recovery = recovery
            outcome.duration_s = round(self._clock() - started, 3)
            self._observer.experiment_finished(outcome)
            outcomes.append(outcome)
        return outcomes

    def _blocked(self, worker: str, candidate_id: str | None) -> str | None:
        if self._before_kill is None:
            return None
        try:
            self._before_kill(worker, candidate_id)
        except KillBlocked as error:
            return f"{error.code}: {error.message}"
        except Exception as error:      # noqa: BLE001  (a check that could not complete refuses: fail closed)
            return f"KILL_CHECK_FAILED: {error}"
        return None

    def _run_candidate(self, candidate: LoopCandidate) -> CandidateOutcome:
        keys = self.planned_keys(candidate)
        for key in keys:
            self._observer.experiment_planned(key)
        outcomes: list[ExperimentOutcome] = []
        result = CandidateOutcome(candidate.candidate_id, CandidateStatus.RUNNING)
        self._observer.stage(None, "PREPARE", f"candidate {candidate.candidate_id}")
        executor = self._factory(candidate.candidate_id)
        applied = False
        run_stop: _RunStop | None = None
        try:
            self.authority.authorize_apply(executor.target, candidate.candidate_id, candidate.yaml_sha256)
            before = executor.observe()
            applied = True      # set before the write: a patch that raised may still have been partly applied
            self._observer.stage(None, "APPLY_CANDIDATE", candidate.candidate_id)
            executor.apply(candidate.yaml_text)
            self._observer.stage(None, "VERIFY_ROLLOUT", candidate.candidate_id)
            if not executor.await_settled():
                result.reason = "The rollout did not settle, so the placement is not final."
            else:
                placed = executor.observe()
                stop_reason: str | None = None
                for key in keys:
                    if stop_reason is not None:
                        continue
                    self._check_abort()
                    outcome = self._run_experiment(executor, candidate, key, before, placed)
                    outcomes.append(outcome)
                    if self._manual_restore:
                        raise _RunStop(RunStatus.NEEDS_MANUAL_RESTORE, f"{key.worker} could not be restored.")
                    if outcome.status is not ExperimentStatus.PASS:
                        stop_reason = (f"Stopped after {key.worker} (repeat {key.repeat}): this candidate "
                                       f"{'failed' if outcome.status is ExperimentStatus.FAIL else 'could not be fully validated'}.")
                result.reason = stop_reason
        except _RunStop as stop:
            run_stop, result.reason = stop, stop.reason      # the candidate is still put back below before the run ends
        except ValidationRefusedError as error:
            result.reason = f"{error.code}: {error.message}"
            if error.code.startswith("GRANT_"):      # outside what the person approved: nothing else may run
                run_stop = _RunStop(RunStatus.INTERRUPTED, result.reason)
        except Exception as error:      # noqa: BLE001
            result.reason = f"The candidate could not be applied or observed: {error}"
        done = {o.key for o in outcomes}
        outcomes += [self._skip(key, result.reason or "Not run.") for key in keys if key not in done]
        status, reason = judge_candidate(len(keys), outcomes)
        result.status, result.reason = status, result.reason if status is not CandidateStatus.PASSED else None
        if result.reason is None and reason:
            result.reason = reason
        self._close(executor, result, outcomes, applied)
        self._finished.append(result)
        if run_stop is not None:
            raise run_stop
        return result

    def _close(self, executor: ValidationExecutor, result: CandidateOutcome, outcomes: list[ExperimentOutcome],
               applied: bool) -> CandidateOutcome:
        """Restore the candidate's Deployments. A revert that fails is flagged: the cluster then needs a person."""
        result.experiments = outcomes
        if applied:
            self._observer.stage(None, "RESTORE_DEPLOYMENT", result.candidate_id)
            try:
                executor.revert()
                result.reverted = True
            except Exception as error:      # noqa: BLE001
                result.reverted, self._manual_restore = False, True
                self._log("REVERT_FAILED", f"{result.candidate_id}: {error}")
        self._observer.candidate_finished(result)
        return result

    def _run_experiment(self, executor: ValidationExecutor, candidate: LoopCandidate, key: ExperimentKey,
                        before: DeploymentGraph, placed: DeploymentGraph) -> ExperimentOutcome:
        started = self._clock()
        blocked = self._blocked(key.worker, candidate.candidate_id)
        if blocked:
            outcome = ExperimentOutcome(key=key, status=ExperimentStatus.INCONCLUSIVE, reasons=[f"Blocked before the kill: {blocked}"])
            self._observer.experiment_finished(outcome)
            return outcome
        predicted = candidate.predicted.get(key.worker)
        if predicted is None:
            outcome = ExperimentOutcome(key=key, status=ExperimentStatus.INCONCLUSIVE,
                                        reasons=[f"No prediction was made for {key.worker}, so nothing can be compared."])
            self._observer.experiment_finished(outcome)
            return outcome
        graph, probe, problems, recovery = self._inject(executor, key, key.worker, baseline=False)
        if self._manual_restore:
            outcome = ExperimentOutcome(key=key, status=ExperimentStatus.INCONCLUSIVE, recovery=recovery,
                                        reasons=problems + [f"{key.worker} could not be restored."])
            self._observer.experiment_finished(outcome)
            return outcome      # the candidate loop sees the flag, puts the candidate back and ends the run
        if graph is None:
            outcome = ExperimentOutcome(key=key, status=ExperimentStatus.INCONCLUSIVE, reasons=problems, recovery=recovery,
                                        probe=probe)
        else:
            self._observer.stage(key, "PROBE", "judging the workflow check and readiness")
            readiness = mandatory_readiness(graph, self.workflow.mandatory_services, key.worker)
            comparison: dict[str, Any] = {}
            error: float | None = None
            try:
                after = ObservedOutcome(predict_failure_impact(self.workflow, graph, key.worker), MEASURED)
                baseline = self._baselines.get(key.worker)
                if baseline is None:
                    baseline = ObservedOutcome(predict_failure_impact(self.workflow, before, key.worker), DERIVED)
                comparison, _validated, _reasons, error = compare_outcomes(predicted, baseline, after, self.workflow,
                                                                           self._thresholds, placed)
            except ValueError as problem:      # Component 3 could not read the observed placement
                problems.append(f"the observation could not be evaluated ({problem})")
            outcome = judge_experiment(key, readiness, probe, probe_required=self._probe_required, comparison=comparison,
                                       prediction_error=error, predicted={"failedNode": predicted.failed_node,
                                       "blastRadiusAfter": predicted.after.blast_radius,
                                       "workflowSurvives": predicted.workflow_id not in predicted.after.failed_workflows})
            if problems:
                outcome.reasons += problems
                if outcome.status is ExperimentStatus.PASS:
                    outcome.status = ExperimentStatus.INCONCLUSIVE
            outcome.recovery = recovery
        outcome.duration_s = round(self._clock() - started, 3)
        self._observer.experiment_finished(outcome)
        return outcome

    # -- the run -------------------------------------------------------------------------------------------------

    def run(self) -> LoopResult:
        result = LoopResult(RunStatus.RUNNING)
        self._observer.run_started(self.plan, [c.candidate_id for c in self.candidates])
        try:
            self._run_baselines()
            for candidate in self.candidates:
                self._check_abort()
                self._run_candidate(candidate)
                if self._manual_restore:
                    raise _RunStop(RunStatus.NEEDS_MANUAL_RESTORE, "A worker or a Deployment could not be restored.")
            result.status = RunStatus.COMPLETED
        except _RunStop as stop:
            result.status, result.stop_reason = stop.status, stop.reason
            self._skip_remaining(result)
        except ValidationRefusedError as error:
            result.status, result.stop_reason = RunStatus.INTERRUPTED, f"{error.code}: {error.message}"
            self._skip_remaining(result)
        result.candidates, result.baseline = list(self._finished), list(self._baseline_outcomes)
        result.needs_manual_restore = self._manual_restore
        if self._manual_restore:
            result.status = RunStatus.NEEDS_MANUAL_RESTORE
        order = {c.candidate_id: c.change_key for c in self.candidates}
        result.winner = select_winner(result.candidates, order) if result.status is RunStatus.COMPLETED else None
        result.kills_used = self.authority.budget.used(self.authority.grant.run_id)
        result.events = self._events
        return result

    def _skip_remaining(self, result: LoopResult) -> None:
        """Candidates the run never reached are recorded as SKIPPED, with every one of their planned experiments."""
        reason = result.stop_reason or "The run ended early."
        seen = {c.candidate_id for c in self._finished}
        for candidate in self.candidates:
            if candidate.candidate_id in seen:
                continue
            skipped = [self._skip(key, reason) for key in self.planned_keys(candidate)]
            outcome = CandidateOutcome(candidate.candidate_id, CandidateStatus.SKIPPED, skipped, reason)
            self._observer.candidate_finished(outcome)
            self._finished.append(outcome)
