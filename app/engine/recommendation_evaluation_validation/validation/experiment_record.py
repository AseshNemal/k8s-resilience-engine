"""The experiment record: everything a validation run knew and saw, with the prediction and the observation kept apart.

A record is written twice. At PLAN time it holds what was decided before anything ran: the cluster, the workflow, the
placement the model started from, the selected recommendation, the exact YAML and the approval it was approved under, and the
PREDICTION. At END time it gains the OBSERVATION, the comparison, the prediction error, the verdict, and what was restored.
The prediction columns are never rewritten (the repository refuses it), so a stored experiment can always be read as "this is
what we said would happen, and this is what happened".

What a record may be used for is decided by ``evidence_class`` and ``is_measurement`` alone. A run on a test double is stored
(it exercises the machinery) but is never a measurement; a run that ended FAILED, INCONCLUSIVE, INTERRUPTED, REFUSED or ABORTED
has no verdict and no prediction error, whatever was seen along the way.

The two bases are stored in words, because they are the limits of any claim made from a record:

* the prediction is Component 3's ``predict_failure_impact`` applied to the topology the model simulated;
* the observation is the same Component 3 rules applied to pod readiness read from the cluster after the node stopped. It is
  readiness-derived. It is not an end-to-end request probe of the workflow.
"""

import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from app.engine.recommendation_evaluation_validation.approval import ApprovalModel
from app.engine.recommendation_evaluation_validation.ranking import FORMULA_VERSION
from app.engine.recommendation_evaluation_validation.validation.cluster_gateway import TEST_DOUBLE
from app.engine.recommendation_evaluation_validation.validation.comparison import observed_placement, workflow_services
from app.engine.recommendation_evaluation_validation.validation.models import (
    Authorization,
    RunState,
    ValidationRefusedError,
    ValidationResult,
    ValidationThresholds,
)
from app.engine.recommendation_evaluation_validation.validation.run import ValidationPlan, ValidationRun
from app.shared.schemas.failure_impact import FailureImpactReport
from app.shared.schemas.workflow import WorkflowGraph

PREDICTION_BASIS = ("Component 3 predict_failure_impact applied to the current topology and to the virtual topology of the "
                    "selected candidate; fixed before anything was applied.")
OBSERVATION_BASIS = ("Readiness-derived: Component 3's survival rules applied to pod readiness read from the cluster after the "
                     "node was stopped. It is not an end-to-end request probe of the workflow.")
REFUSED, ABORTED = "REFUSED", "ABORTED"


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    recommendation_id: str
    approval_id: int | None
    workflow_id: str
    status: str
    validated: bool | None
    is_measurement: bool
    evidence_class: str
    cluster: dict[str, Any]
    workflow: dict[str, Any]
    before_evidence: dict[str, Any]
    recommendation: dict[str, Any]
    proposal: dict[str, Any]
    predicted: dict[str, Any]
    observed: dict[str, Any] | None
    comparison: dict[str, Any] | None
    prediction_error: float | None
    failure: dict[str, Any] | None
    events: list[dict[str, Any]]
    provenance: dict[str, Any]
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RecordStore(Protocol):
    def create(self, record: ExperimentRecord) -> None: ...
    def finalize(self, record: ExperimentRecord) -> None: ...
    def get(self, experiment_id: str) -> ExperimentRecord: ...


def _dump(report: FailureImpactReport) -> dict[str, Any]:
    return report.model_dump(mode="json", by_alias=True)


def _survival(report: FailureImpactReport, workflow: WorkflowGraph) -> tuple[dict[str, bool], bool]:
    services = {name: name not in report.failed_services for name in workflow_services(workflow)}
    return services, workflow.workflow_id not in report.failed_workflows


def _predicted(plan: ValidationPlan) -> dict[str, Any]:
    p = plan.predicted
    services, survives = _survival(p.after, plan.workflow)
    return {
        "failedNode": p.failed_node, "workflowId": p.workflow_id, "fingerprint": p.fingerprint,
        "before": _dump(p.before), "after": _dump(p.after),
        "blastRadiusBefore": p.before.blast_radius, "blastRadiusAfter": p.after.blast_radius, "riskReduction": p.risk_reduction,
        "serviceSurvivalAfter": services, "workflowSurvivesAfter": survives,
        "failedServicesAfter": list(p.after.failed_services), "placement": p.placement,
    }


def _approval_reference(approval: ApprovalModel) -> dict[str, Any]:
    def when(value: datetime | None) -> str | None:
        return value.isoformat() if value else None
    return {"id": approval.id, "status": approval.status.value, "proposalVersion": approval.proposal_version,
            "requestedBy": approval.requested_by, "requestedAt": when(approval.requested_at),
            "decidedBy": approval.decided_by, "decidedAt": when(approval.decided_at), "decisionNote": approval.decision_note}


def _describe(executor: Any) -> dict[str, Any]:
    describe = getattr(executor, "describe", None)
    if callable(describe):
        return describe()
    return {"evidenceClass": TEST_DOUBLE, "target": executor.target.name, "executor": type(executor).__name__, "cluster": {}}


def planned_record(plan: ValidationPlan, approval: ApprovalModel, executor: Any, thresholds: ValidationThresholds, *,
                   experiment_id: str, now: datetime) -> ExperimentRecord:
    environment = _describe(executor)
    baseline = getattr(executor, "baseline_graph", None)
    snapshot = approval.evaluation_snapshot or {}
    return ExperimentRecord(
        experiment_id=experiment_id, recommendation_id=plan.recommendation_id, approval_id=approval.id,
        workflow_id=plan.workflow.workflow_id, status=RunState.PLANNED.value, validated=None, is_measurement=False,
        evidence_class=environment.get("evidenceClass", TEST_DOUBLE), cluster=environment,
        workflow=plan.workflow.model_dump(mode="json", by_alias=True),
        before_evidence={"failedNode": plan.predicted.failed_node,
                         "deploymentBefore": baseline.model_dump(mode="json", by_alias=True) if baseline is not None else None,
                         "topologySnapshotId": (snapshot.get("proposal") or {}).get("topologySnapshotId")},
        recommendation=dict(approval.recommendation_snapshot or {}),
        proposal={"yaml": plan.yaml_text, "yamlSha256": plan.yaml_sha256, "fingerprint": plan.predicted.fingerprint,
                  "approval": _approval_reference(approval)},
        predicted=_predicted(plan), observed=None, comparison=None, prediction_error=None, failure=None,
        events=[], provenance={
            "approvalProvenance": dict(snapshot.get("provenance") or {}), "thresholds": asdict(thresholds),
            "rankingFormulaVersion": FORMULA_VERSION, "planDigest": plan.digest, "yamlSha256": plan.yaml_sha256,
            "predictionBasis": PREDICTION_BASIS, "observationBasis": OBSERVATION_BASIS, "environment": environment},
        created_at=now)


def _observed(run: ValidationRun) -> dict[str, Any] | None:
    plan, observations, graphs = run.plan, run.observations, run.captured
    if not graphs and not any(observations.values()):
        return None
    observed: dict[str, Any] = {"failedNode": plan.predicted.failed_node, "basis": OBSERVATION_BASIS}
    baseline, after = observations["baseline"], observations["afterFailure"]
    if baseline is not None:
        observed["baseline"] = {"basis": baseline.basis, "blastRadius": baseline.report.blast_radius,
                                "report": _dump(baseline.report)}
    if after is not None:
        services, survives = _survival(after.report, plan.workflow)
        observed["afterFailure"] = {"blastRadius": after.report.blast_radius, "failedServices": list(after.report.failed_services),
                                    "failedWorkflows": list(after.report.failed_workflows), "serviceSurvival": services,
                                    "workflowSurvives": survives, "report": _dump(after.report)}
        if baseline is not None:
            observed["riskReduction"] = round(baseline.report.blast_radius - after.report.blast_radius, 2)
    if "placed" in graphs:
        observed["placement"] = observed_placement(graphs["placed"], list(plan.predicted.placement))
    observed["graphs"] = {name: graph.model_dump(mode="json", by_alias=True) for name, graph in graphs.items()}
    return observed


def _node_restored(events: list[dict[str, Any]]) -> bool | None:
    names = [event["event"] for event in events]
    if "RESTORE_FAILED" in names:
        return False
    return True if "NODE_RESTORED" in names else None


PROBE_NOTE = ("The probe is recorded beside the readiness-derived comparison, not instead of it: the verdict is still the "
              "readiness-derived one. Whether a probe disagreement should block a verdict is a decision to make before the "
              "first real run, not after seeing one.")


def _probe_comparison(planned: ExperimentRecord, observed: dict[str, Any] | None,
                      probes: dict[str, Any]) -> dict[str, Any] | None:
    after, baseline = probes.get("afterChange"), probes.get("baselineFailure")
    if after is None:
        return None
    predicted = planned.predicted["workflowSurvivesAfter"]
    probe, base = after["workflowSurvives"], (baseline or {}).get("workflowSurvives")
    readiness = ((observed or {}).get("afterFailure") or {}).get("workflowSurvives")
    return {
        "predictedSurvives": predicted, "probeSurvives": probe, "readinessSurvives": readiness,
        "matchesPrediction": None if probe is None else probe == predicted,
        "agreesWithReadiness": None if probe is None or readiness is None else probe == readiness,
        "baselineProbeSurvives": base,
        "observedImprovement": None if probe is None or base is None else (probe and not base),
        "note": PROBE_NOTE}


def finished_record(planned: ExperimentRecord, run: ValidationRun, result: ValidationResult | None, *, now: datetime,
                    status: str | None = None, stop_reason: str | None = None) -> ExperimentRecord:
    """The planned record with the outcome of ``run`` added. ``result`` None means the run raised before a verdict."""
    ended = status or result.state.value
    measured = result is not None and result.state is RunState.COMPLETED
    reasons = (result.reasons[0] if result and result.reasons else None) if result and not measured else None
    events = [dict(event) for event in run.events]
    observed = _observed(run)
    reader = getattr(run.executor, "probe_reports", None)
    probes = reader() if callable(reader) else {}
    if probes:
        observed = {**(observed or {"failedNode": run.plan.predicted.failed_node, "basis": OBSERVATION_BASIS}), "probes": probes}
    comparison = dict(result.comparison) if measured and result.comparison else None
    if comparison is not None and probes:
        entry = _probe_comparison(planned, observed, probes)
        if entry is not None:
            comparison["probe"] = entry
    started = next((event["at"] for event in events if event["event"] != "PLANNED"), None)
    return ExperimentRecord(
        **{**planned.__dict__,
           "status": ended, "validated": result.validated if measured else None, "is_measurement": measured,
           "observed": observed, "comparison": comparison,
           "prediction_error": result.prediction_error if measured else None,
           "failure": {"runEnded": ended, "stopReason": stop_reason or reasons,
                       "needsManualRestore": run.needs_manual_restore, "workloadsReverted": run.workloads_reverted,
                       "nodeRestored": _node_restored(events)},
           "events": events, "started_at": datetime.fromisoformat(started) if started else None, "finished_at": now})


class ExperimentRecorder:
    """Writes the experiment record around a ``ValidationRun``: at plan time, and when the run ends however it ends."""

    def __init__(self, store: RecordStore, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._store, self._clock = store, clock

    def start(self, plan: ValidationPlan, approval: ApprovalModel, executor: Any, thresholds: ValidationThresholds) -> str:
        now = self._clock()
        experiment_id = f"exp-{now:%Y%m%dT%H%M%SZ}-{plan.digest[:8]}-{secrets.token_hex(3)}"      # unique even for one plan twice
        self._store.create(planned_record(plan, approval, executor, thresholds, experiment_id=experiment_id, now=now))
        return experiment_id

    def finish(self, experiment_id: str, run: ValidationRun, result: ValidationResult) -> ExperimentRecord:
        record = finished_record(self._store.get(experiment_id), run, result, now=self._clock())
        self._store.finalize(record)
        return record

    def execute(self, run: ValidationRun, experiment_id: str, apply: Authorization, failure: Authorization,
                baseline: Authorization | None = None) -> ValidationResult:
        """``run.execute`` with the outcome stored whatever it is: a verdict, a refusal, or an unexpected error."""
        try:
            result = run.execute(apply, failure, baseline)
        except ValidationRefusedError as error:
            self._store.finalize(finished_record(self._store.get(experiment_id), run, None, now=self._clock(),
                                                 status=REFUSED, stop_reason=str(error)))
            raise
        except BaseException as error:
            self._store.finalize(finished_record(self._store.get(experiment_id), run, None, now=self._clock(),
                                                 status=ABORTED, stop_reason=f"{type(error).__name__}: {error}"))
            raise
        self.finish(experiment_id, run, result)
        return result
