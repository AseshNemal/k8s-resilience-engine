"""The validation run: apply an approved change to a TEST cluster, break a node on purpose, compare with the prediction.

Nothing here can touch a cluster by itself. All contact goes through a ``ValidationExecutor`` that the caller
supplies, and the only executors that exist are test doubles (``tests/support``). A real executor, one that
patches a Deployment and cordons or stops a node, is deliberately NOT built here: it needs a decision about what a
test cluster is and how it is identified, and it must be reviewed before it can write anywhere.

The guards, each of which refuses before anything changes:
* the plan is built only from an APPROVED request with verified provenance whose proposal fingerprint and YAML digest
  still match a fresh evaluation (a stale approval is refused, not re-interpreted);
* the target must be a TEST environment, and the executor must be pointed at that same target;
* every write (the apply) and every controlled failure needs its own ``Authorization`` bound to the exact plan, the
  action and the target. One authorization cannot stand in for another;
* the apply re-checks that the approval is still current immediately before it writes.

What happens if something goes wrong. An executor error ends the run FAILED, a cut-short run INTERRUPTED, unmeasurable
or unsettled evidence INCONCLUSIVE. A node taken down for a failure is always restored in a ``finally``, and the
Deployments the run wrote are reverted once the run ends, in whatever state; if either restore fails the result says
``needsManualRestore``. The revert is covered by the apply authorization: it returns the cluster to the state the person
authorized changing, so refusing it for want of a second signature would leave the test cluster dirty. None of these
states can ever be ``validated``.

Validation outcomes are separate from the simulation: the prediction is fixed into the plan before anything runs and is
never rewritten by what was observed.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.recommendation_evaluation_validation.approval import (
    ApprovalModel,
    LiveRecommendation,
    has_verified_provenance,
)
from app.engine.recommendation_evaluation_validation.approval.eligibility import evaluate_eligibility
from app.engine.recommendation_evaluation_validation.validation.comparison import compare_outcomes
from app.engine.recommendation_evaluation_validation.validation.models import (
    APPLY,
    DERIVED,
    FAIL_BASELINE,
    FAIL_NODE,
    MEASURED,
    TERMINAL_STATES,
    TEST_ENVIRONMENT,
    Authorization,
    ObservedOutcome,
    PredictedOutcome,
    RunState,
    TestTarget,
    ValidationInterrupted,
    ValidationRefusedError,
    ValidationResult,
    ValidationThresholds,
)
from app.engine.recommendation_evaluation_validation.validation.prediction import predict_outcome
from app.engine.recommendation_evaluation_validation.yaml_proposal import GENERATED, build_yaml_proposal
from app.shared.schemas.approval import ApprovalStatus
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.validation import ValidationReport
from app.shared.schemas.workflow import WorkflowGraph


class ValidationExecutor(Protocol):
    """Everything a run needs from a test environment. The run never reaches a cluster any other way."""

    target: TestTarget

    def observe(self) -> DeploymentGraph:
        """The current placement and readiness, read only."""

    def apply(self, yaml_text: str) -> None:
        """Write the proposal to the test cluster."""

    def await_settled(self) -> bool:
        """Wait for the rollout; False when it did not settle in time."""

    def fail_node(self, node: str) -> DeploymentGraph:
        """Take the node down on purpose and return the placement and readiness observed afterwards."""

    def restore_node(self, node: str) -> None:
        """Bring the node back."""

    def revert(self) -> None:
        """Put every Deployment this run wrote back to what it was before the apply, and wait for it to settle.

        Called whenever an apply was attempted, even one that failed halfway (it may have written part of the patch).
        """


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ValidationPlan:
    """What is allowed to be validated: an approved proposal, still current, with its prediction fixed."""

    def __init__(self, approval: ApprovalModel, workflow: WorkflowGraph, target: TestTarget,
                 predicted: PredictedOutcome, yaml_text: str, yaml_sha256: str) -> None:
        self.approval_id, self.recommendation_id = approval.id, approval.recommendation_id
        self.workflow, self.target, self.predicted = workflow, target, predicted
        self.yaml_text, self.yaml_sha256 = yaml_text, yaml_sha256
        self.digest = _sha256(json.dumps({
            "fingerprint": predicted.fingerprint, "yaml": yaml_sha256, "failedNode": predicted.failed_node,
            "target": target.name}, sort_keys=True))

    @staticmethod
    def check_current(approval: ApprovalModel, live: LiveRecommendation) -> str:
        """Refuse unless ``approval`` is still the proposal ``live`` recommends; returns the YAML digest."""
        recommendation_id = approval.recommendation_id
        proposal = (approval.evaluation_snapshot or {}).get("proposal") or {}
        recorded_yaml = proposal.get("yaml") or {}
        standing = evaluate_eligibility(live, recommendation_id)
        if not standing.eligible or standing.fingerprint != proposal.get("fingerprint"):
            raise ValidationRefusedError("APPROVAL_STALE", "The approval is no longer for what the current evaluation "
                                         "recommends (the topology or the plan moved), so nothing is written.")
        if (live.evidence or {}).get("failedNode") != proposal.get("failedNode"):
            raise ValidationRefusedError("APPROVAL_STALE", "The failure the approval was assessed against has changed.")
        regenerated = build_yaml_proposal(live, recommendation_id)
        if (recorded_yaml.get("origin") != "server-generated" or regenerated.status != GENERATED
                or regenerated.sha256 != recorded_yaml.get("sha256") or approval.yaml_snapshot != regenerated.yaml):
            raise ValidationRefusedError("APPROVAL_STALE", "The YAML the server generates now is not the YAML that was "
                                         "approved, or the approved YAML was not server-generated.")
        return regenerated.sha256

    @classmethod
    def from_approval(cls, approval: ApprovalModel, live: LiveRecommendation, workflow: WorkflowGraph,
                      target: TestTarget) -> "ValidationPlan":
        if approval.status is not ApprovalStatus.APPROVED:
            raise ValidationRefusedError("APPROVAL_NOT_APPROVED", f"The request is {approval.status.value}; only an "
                                         f"APPROVED request can be validated.")
        if not has_verified_provenance(approval):
            raise ValidationRefusedError("APPROVAL_UNVERIFIED", "The approval has no verified link to live evidence.")
        if target.environment != TEST_ENVIRONMENT:
            raise ValidationRefusedError("TARGET_NOT_TEST_ENVIRONMENT", f"{target.name!r} is a {target.environment!r} "
                                         f"environment; a validation run only writes to and breaks a test environment.")
        sha = cls.check_current(approval, live)
        predicted = predict_outcome(workflow, live, approval.recommendation_id)
        return cls(approval, workflow, target, predicted, approval.yaml_snapshot, sha)


class ValidationRun:
    """One validation of one approved plan. Steps are explicit; each write or failure needs its own authorization."""

    def __init__(self, plan: ValidationPlan, executor: ValidationExecutor, *,
                 thresholds: ValidationThresholds | None = None,
                 current_evaluation: Callable[[], LiveRecommendation] | None = None,
                 approval: ApprovalModel | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.plan, self.executor, self.thresholds = plan, executor, thresholds or ValidationThresholds()
        self._current, self._approval, self._clock = current_evaluation, approval, clock
        self.state, self.events = RunState.PLANNED, []
        self._baseline: ObservedOutcome | None = None
        self._after: ObservedOutcome | None = None
        self._placed: DeploymentGraph | None = None
        self._before_graph: DeploymentGraph | None = None
        self._baseline_failure_graph: DeploymentGraph | None = None
        self._after_graph: DeploymentGraph | None = None
        self._stop: str | None = None
        self._manual_restore = False
        self._apply_attempted = False
        self._reverted: bool | None = None
        self._result: ValidationResult | None = None
        self._log("PLANNED", f"target {plan.target.name}, plan {plan.digest[:12]}")

    # -- bookkeeping ----------------------------------------------------------------------------------------

    @property
    def captured(self) -> dict[str, DeploymentGraph]:
        """The placements this run actually observed, for the record. Only what was observed is present."""
        graphs = {"baseline": self._before_graph, "baselineFailure": self._baseline_failure_graph,
                  "placed": self._placed, "afterFailure": self._after_graph}
        return {name: graph.model_copy(deep=True) for name, graph in graphs.items() if graph is not None}

    @property
    def workloads_reverted(self) -> bool | None:
        return self._reverted

    @property
    def needs_manual_restore(self) -> bool:
        return self._manual_restore

    @property
    def observations(self) -> dict[str, ObservedOutcome | None]:
        """The two measured (or derived) outcomes, with the basis each one stands on."""
        return {"baseline": self._baseline, "afterFailure": self._after}

    def _log(self, event: str, detail: str = "") -> None:
        self.events.append({"at": self._clock().isoformat(), "event": event, "detail": detail})

    def _require(self, *states: RunState) -> None:
        if self.state not in states:
            raise ValidationRefusedError("WRONG_STATE", f"This step needs the run to be {' or '.join(s.value for s in states)}, "
                                         f"it is {self.state.value}.")

    def _authorize(self, authorization: Authorization, action: str) -> None:
        if self.executor.target != self.plan.target:
            raise ValidationRefusedError("TARGET_MISMATCH", "The executor is not pointed at the plan's target.")
        if self.executor.target.environment != TEST_ENVIRONMENT:
            raise ValidationRefusedError("TARGET_NOT_TEST_ENVIRONMENT", "Only a test environment may be written to.")
        if authorization is None or not (authorization.confirmed_by or "").strip():
            raise ValidationRefusedError("AUTHORIZATION_MISSING", f"{action} needs an explicit authorization by a named person.")
        if (authorization.action != action or authorization.plan_digest != self.plan.digest
                or authorization.target != self.plan.target.name):
            raise ValidationRefusedError("AUTHORIZATION_MISMATCH", f"The authorization is not for {action} of this plan on "
                                         f"{self.plan.target.name}.")

    def _stopped(self, state: RunState, why: str) -> None:
        self.state, self._stop = state, why
        self._log(state.value, why)

    def _predict(self, graph: DeploymentGraph, node: str):
        return predict_failure_impact(self.plan.workflow, graph, node)

    def _fail_and_restore(self, node: str) -> DeploymentGraph:
        """Fail the node, and bring it back whatever happens. A restore that fails is flagged, never swallowed."""
        try:
            return self.executor.fail_node(node)
        finally:
            try:
                self.executor.restore_node(node)
                self._log("NODE_RESTORED", node)
            except Exception as error:      # noqa: BLE001  (the node may still be down: say so)
                self._manual_restore = True
                self._log("RESTORE_FAILED", f"{node}: {error}")

    def _step(self, stage: str, action: Callable[[], Any]) -> bool:
        """Run one executor-touching step; an executor failure ends the run instead of escaping half-done."""
        try:
            action()
            return True
        except ValidationRefusedError:
            raise
        except ValidationInterrupted as error:
            self._stopped(RunState.INTERRUPTED, f"{stage}: {error}")
        except ValueError as error:      # Component 3 could not read the observed placement
            self._stopped(RunState.INCONCLUSIVE, f"{stage}: the observation could not be evaluated ({error})")
        except Exception as error:      # noqa: BLE001
            self._stopped(RunState.FAILED, f"{stage}: {error}")
        return False

    # -- steps ----------------------------------------------------------------------------------------------

    def measure_baseline(self, authorization: Authorization) -> RunState:
        """Optional: fail the node BEFORE the change, so the observed risk reduction is a measurement."""
        self._require(RunState.PLANNED)
        self._authorize(authorization, FAIL_BASELINE)
        self._log("AUTHORIZED", f"{FAIL_BASELINE} by {authorization.confirmed_by}")

        def run() -> None:
            graph = self._fail_and_restore(self.plan.predicted.failed_node)
            self._baseline_failure_graph = graph.model_copy(deep=True)
            self._baseline = ObservedOutcome(self._predict(graph, self.plan.predicted.failed_node), MEASURED)
            self.state = RunState.BASELINE_MEASURED
            self._log("BASELINE_MEASURED")
        self._step("baseline", run)
        return self.state

    def apply(self, authorization: Authorization) -> RunState:
        """Write the approved YAML to the test cluster. Needs its own authorization and a still-current approval."""
        self._require(RunState.PLANNED, RunState.BASELINE_MEASURED)
        self._authorize(authorization, APPLY)
        if self._current is not None and self._approval is not None:
            ValidationPlan.check_current(self._approval, self._current())      # refuses with APPROVAL_STALE before any write
        self._log("AUTHORIZED", f"{APPLY} by {authorization.confirmed_by}")

        def run() -> None:
            self._before_graph = self.executor.observe()
            if self._baseline is None:
                self._baseline = ObservedOutcome(self._predict(self._before_graph, self.plan.predicted.failed_node), DERIVED)
            self._apply_attempted = True      # set before the write: a patch that raised may still have been partly applied
            self.executor.apply(self.plan.yaml_text)
            self.state = RunState.APPLIED
            self._log("APPLIED", f"yaml {self.plan.yaml_sha256[:12]}")
        self._step("apply", run)
        return self.state

    def observe_placement(self) -> RunState:
        """Read-only: wait for the rollout and record where the scheduler actually put the pods."""
        self._require(RunState.APPLIED)

        def run() -> None:
            if not self.executor.await_settled():
                self._stopped(RunState.INCONCLUSIVE, "the rollout did not settle, so the placement is not final")
                return
            self._placed = self.executor.observe()
            self.state = RunState.PLACEMENT_OBSERVED
            self._log("PLACEMENT_OBSERVED")
        self._step("placement", run)
        return self.state

    def inject_failure(self, authorization: Authorization) -> RunState:
        """Take the predicted node down in the test cluster, observe readiness, and restore the node."""
        self._require(RunState.PLACEMENT_OBSERVED)
        self._authorize(authorization, FAIL_NODE)
        self._log("AUTHORIZED", f"{FAIL_NODE} by {authorization.confirmed_by}")

        def run() -> None:
            graph = self._fail_and_restore(self.plan.predicted.failed_node)
            self._after_graph = graph.model_copy(deep=True)
            self._after = ObservedOutcome(self._predict(graph, self.plan.predicted.failed_node), MEASURED)
            self.state = RunState.FAILURE_OBSERVED
            self._log("FAILURE_OBSERVED")
        if not self._step("failure", run) and self._manual_restore:
            self._log("NEEDS_MANUAL_RESTORE", self.plan.predicted.failed_node)
        return self.state

    def revert_workloads(self) -> bool | None:
        """Put the written Deployments back (once). None when nothing was applied; False when the revert failed."""
        if not self._apply_attempted or self._reverted is not None:
            return self._reverted
        try:
            self.executor.revert()
            self._reverted = True
            self._log("WORKLOADS_REVERTED")
        except Exception as error:      # noqa: BLE001  (the Deployments may still carry the patch: say so)
            self._reverted, self._manual_restore = False, True
            self._log("REVERT_FAILED", str(error))
        return self._reverted

    def finish(self) -> ValidationResult:
        """The verdict, asked for as often as needed: a run that has ended always gives the same one.

        Anything the run wrote is reverted first, so the verdict describes a cluster that has been put back.
        """
        if self._result is not None:
            return self._result
        self.revert_workloads()
        result = self._verdict()
        if result.state in TERMINAL_STATES:
            self._result = result
        return result

    def _verdict(self) -> ValidationResult:
        p = self.plan.predicted
        predicted = {"failedNode": p.failed_node, "blastRadiusBefore": p.before.blast_radius,
                     "blastRadiusAfter": p.after.blast_radius, "riskReduction": p.risk_reduction,
                     "failedServicesAfter": list(p.after.failed_services), "placement": p.placement}
        if self.state is RunState.FAILURE_OBSERVED:
            comparison, validated, reasons, error = compare_outcomes(
                p, self._baseline, self._after, self.plan.workflow, self.thresholds, self._placed)
            self.state = RunState.COMPLETED
            self._log("COMPLETED", "validated" if validated else "not validated")
            return ValidationResult(
                p.recommendation_id, RunState.COMPLETED, validated, reasons, predicted,
                {"blastRadiusBefore": self._baseline.report.blast_radius, "baselineBasis": self._baseline.basis,
                 "blastRadiusAfter": self._after.report.blast_radius,
                 "failedServicesAfter": list(self._after.report.failed_services)},
                comparison, error, self._manual_restore, self._reverted)
        if self.state in TERMINAL_STATES:
            reason = self._stop or self.state.value
        else:
            reason = f"The run stopped at {self.state.value} before the failure was observed."
            self._stopped(RunState.INCONCLUSIVE, reason)      # the cluster has been put back; this run cannot go on
        return ValidationResult(p.recommendation_id, self.state, False, (reason,), predicted, {}, {}, None,
                                self._manual_restore, self._reverted)

    def execute(self, apply: Authorization, failure: Authorization, baseline: Authorization | None = None) -> ValidationResult:
        """All steps in order, stopping at the first run-ending state. Each authorization is still checked per step."""
        steps: list[Callable[[], RunState]] = []
        if baseline is not None:
            steps.append(lambda: self.measure_baseline(baseline))
        steps += [lambda: self.apply(apply), self.observe_placement, lambda: self.inject_failure(failure)]
        try:
            for step in steps:
                if step() in TERMINAL_STATES:
                    break
        except BaseException:
            self.revert_workloads()      # a refusal after the apply must not leave the test cluster patched
            raise
        return self.finish()


def to_validation_report(result: ValidationResult) -> ValidationReport | None:
    """The shared five-field contract, only for a run that produced a measurement; None otherwise."""
    if result.state is not RunState.COMPLETED:
        return None
    return ValidationReport(
        recommendation_id=result.recommendation_id,
        predicted_blast_radius=result.predicted["blastRadiusAfter"],
        observed_blast_radius=result.observed["blastRadiusAfter"],
        prediction_error=result.prediction_error, validated=result.validated)


def authorize(plan: ValidationPlan, action: str, confirmed_by: str) -> Authorization:
    """What a person's explicit confirmation of one step looks like once the UI/CLI has collected it."""
    return Authorization(action, confirmed_by, plan.digest, plan.target.name)
