"""Apply an approved change to the connected cluster, and Reset it.

This is the step the approval gate has always said it does not take: ``approve`` applies nothing. Here a person, naming
themselves, applies a change that has ALREADY been approved, and can put the cluster back. Developer-approved 2026-10-23
for the cluster the extension is connected to; the checks below stay because they cost nothing and a wrong write is not
undone by being fast.

Apply refuses unless, in this order:

* the approval is APPROVED and its provenance is the tier's own (a production approval for the production routes, a research
  approval for the declared-workflow routes), so a request cannot be applied by the other tier's door;
* the evaluation, made again now, still gives the same ranked, eligible candidate with the same fingerprint, and the server
  generates the same YAML the person approved. A topology that moved since the approval is a different change;
* the YAML is the server-generated one (a client-supplied text is never applied), and every document in it passes the
  proposal whitelist against the Deployments in the cluster right now: replicas up by a few, a hostname spread, a required
  hostname anti-affinity, and nothing else;
* no other application is active. One change at a time keeps "Reset" meaning a single, known state.

Before the first write the state being replaced is stored (status APPLYING), so a crash half-way still leaves a way back. Each
patch carries the resourceVersion that was read, so a Deployment edited in between is refused by the API server, not overwritten.
If a patch fails, what was already patched is put back at once. Reset puts back exactly the replica count and placement
constraints that were stored, on the same cluster (checked by the ``kube-system`` UID), and can be retried if it fails.

Nothing is applied by reading, by approving, or by asking for the status.

A patch being accepted is not a rollout, and a rollout is not an analysis. After the patches are accepted the change is
APPLIED with a watched rollout (``rollout.py``: the Deployments' own generation and replica counts, never old ready pods);
each status request reads the cluster and advances that record, which is persisted, so a page refresh or a backend restart
resumes it. When the rollout has completed, the failure impact is recalculated from the freshly observed topology (the tier's
``ChangeAnalyser``, which calls Member 3) and compared with the baseline that was stored before the change
(``analysis.py``). The analysis can fail or be unavailable without hiding the applied change or removing Reset, and it is
retried by its own action, which never applies anything.
"""

import hashlib
import re
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Protocol

from app.engine.recommendation_evaluation_validation.application import operation as operation_view
from app.engine.recommendation_evaluation_validation.application import rollout as rollout_watch
from app.engine.recommendation_evaluation_validation.application.analysis import (
    COMPLETED as ANALYSIS_COMPLETED,
    DIRECTION_APPLY,
    DIRECTION_RESET,
    FAILED as ANALYSIS_FAILED,
    PENDING as ANALYSIS_PENDING,
    UNAVAILABLE as ANALYSIS_UNAVAILABLE,
    AnalysisUnavailable,
    ChangeAnalysis,
    ChangeAnalysisRepository,
    ChangeAnalyser,
    compare,
    plan_reset,
)
from app.engine.recommendation_evaluation_validation.application.models import (
    ACTIVE,
    APPLIED,
    APPLYING,
    FAILED,
    RESET,
    RESET_FAILED,
    RESETTABLE,
    AppliedChange,
)
from app.engine.recommendation_evaluation_validation.application.repository import AppliedChangeRepository
from app.engine.recommendation_evaluation_validation.approval import (
    ApprovalModel,
    ApprovalRepository,
    LiveRecommendation,
    evaluate_eligibility,
)
from app.engine.recommendation_evaluation_validation.validation.models import ValidationRefusedError
from app.engine.recommendation_evaluation_validation.validation.proposal_check import check_proposal
from app.engine.recommendation_evaluation_validation.yaml_proposal import GENERATED, build_yaml_proposal
from app.integrations.kubernetes.client import (
    ApiFailedError,
    AuthFailedError,
    ConnectionFailedError,
    ForbiddenError,
    NotConnectedError,
)
from app.integrations.kubernetes.validation_gateway import ConflictError, ObservedDeployment
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.approval import ApprovalStatus

COMPONENT = "recommendation"
PRODUCTION, RESEARCH = "production", "research"
NAME = re.compile(r"^[^\x00-\x1f]{2,100}$")
WRITES_OFF = ("Writes to the cluster are not enabled on this backend (it is armed with RESILIENCE_ALLOW_CLUSTER_WRITES by the "
              "extension's deployment), so nothing can be applied or reset from here.")
# One Apply or Reset at a time in this process, from the checks to the last patch (the backend is one process; the persisted
# ACTIVE check is what holds across restarts). One status request at a time advances a rollout, and one recalculation per analysis.
_MUTATION = threading.Lock()
_ADVANCE = threading.Lock()
_ANALYSING_GUARD = threading.Lock()
_ANALYSING: set[str] = set()
RESTORED_FIELDS = ("name", "namespace", "resource_version", "generation", "replicas", "selector", "pod_labels",
                   "topology_spread_constraints", "pod_anti_affinity")


class ApplyGateway(Protocol):
    """What Apply and Reset need of the cluster. ``KubernetesTestClusterGateway(connected_cluster=True, allow_writes=True)``."""

    def identity(self) -> Any: ...
    def read_deployments(self, namespace: str) -> Mapping[str, ObservedDeployment]: ...
    def patch_deployment(self, document: dict[str, Any], *, expect_resource_version: str) -> None: ...
    def restore_deployment(self, snapshot: Any) -> None: ...
    def read_rollout(self, namespace: str, names: Sequence[str]) -> Mapping[str, Any]: ...


class ApplicationRefused(EvidenceError):
    def __init__(self, code: str, message: str, *, http_status: int = 409, retryable: bool = False,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(code, message, http_status=http_status, component=COMPONENT, retryable=retryable, details=details)


def _cluster_error(error: Exception) -> ApplicationRefused:
    if isinstance(error, NotConnectedError):
        return ApplicationRefused("CLUSTER_NOT_CONNECTED", "No Kubernetes cluster is connected.", http_status=503)
    if isinstance(error, ConflictError):
        return ApplicationRefused("DEPLOYMENT_CHANGED", "A Deployment was changed in the cluster after it was read, so nothing "
                                  "was overwritten. Review it and apply again.", retryable=True)
    if isinstance(error, ConnectionFailedError):
        return ApplicationRefused("CLUSTER_CONNECTION_FAILED", "Could not reach the connected cluster.", http_status=502,
                                  retryable=True)
    if isinstance(error, AuthFailedError):
        return ApplicationRefused("CLUSTER_AUTH_FAILED", "The cluster rejected the stored credential. It may have expired.",
                                  http_status=502)
    if isinstance(error, ForbiddenError):
        return ApplicationRefused("CLUSTER_FORBIDDEN", f"The connected credential is not allowed to {error.verb} "
                                  f"{error.resource}. Reconnect the cluster in the extension to approve full access.",
                                  http_status=502)
    return ApplicationRefused("CLUSTER_API_FAILED", "The cluster returned an unexpected error.", http_status=502, retryable=True)


_CLUSTER_ERRORS = (NotConnectedError, ConflictError, ConnectionFailedError, AuthFailedError, ForbiddenError, ApiFailedError)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _snapshot(live: ObservedDeployment) -> dict[str, Any]:
    document = asdict(live)
    document.pop("extra", None)
    document["topology_spread_constraints"] = [dict(c) for c in live.topology_spread_constraints]
    return document


def _restorable(snapshot: Mapping[str, Any]) -> ObservedDeployment:
    values = {name: snapshot[name] for name in RESTORED_FIELDS}
    values["topology_spread_constraints"] = tuple(values["topology_spread_constraints"] or ())
    return ObservedDeployment(**values)


def _named(who: str | None) -> str:
    name = (who or "").strip()
    if not NAME.match(name):
        raise ApplicationRefused("APPLIER_REQUIRED", "Say who is applying this change (at least two characters).", http_status=422)
    return name


class ApplicationService:
    def __init__(self, approvals: ApprovalRepository, repository: AppliedChangeRepository,
                 gateway_factory: Callable[[], ApplyGateway], *, tier: str,
                 trusted: Callable[[ApprovalModel], bool], require_applicable: bool = True, max_added_replicas: int = 5,
                 writes_enabled: bool = False, analyses: ChangeAnalysisRepository | None = None,
                 analyser: ChangeAnalyser | None = None, experiments: Any = None) -> None:
        # ``require_applicable`` mirrors the approval service's own hook: a DECLARED workflow's YAML is never "applicable"
        # at the production gate, and the research tier (which approved it, knowing that) turns the requirement off.
        self.approvals, self.repository, self._gateway_factory = approvals, repository, gateway_factory
        self.tier, self._trusted, self._max_added = tier, trusted, max_added_replicas
        self._require_applicable = require_applicable
        self.writes_enabled = writes_enabled      # armed by the deployment, never by a request
        # Without both, a change is still applied, watched and reset; only the failure-impact comparison is not recorded.
        self.analyses, self.analyser = analyses, analyser
        # The validation subsystem's experiment store, read only: genuine controlled-experiment measurements for the recommendation,
        # shown apart from the recalculation. Nothing here runs or starts an experiment.
        self.experiments = experiments

    # -- which cluster a change belongs to ---------------------------------------------------------------------------

    def _connected_uid(self) -> str | None:
        """The kube-system UID of the connected cluster, or None when it cannot be read just now (not connected, unreachable, or
        the credential is renewing). None means *unknown*, never "a different cluster"."""
        try:
            return getattr(self._gateway_factory().identity(), "cluster_uid", None) or None
        except _CLUSTER_ERRORS:
            return None

    @staticmethod
    def _cluster_match(change: AppliedChange | None, uid: str | None) -> str | None:
        """"same", "other" or "unknown": where the connected cluster stands against the one this change was applied to."""
        if change is None:
            return None
        stored = (change.cluster or {}).get("clusterUid")
        if not uid or not stored:
            return "unknown"
        return "same" if uid == stored else "other"

    def _active_here(self, uid: str | None) -> list[AppliedChange]:
        """The applied changes that stand on the connected cluster. Each cluster is independent, so a change left on another one
        blocks nothing here; when the connected cluster cannot be identified, every change counts (the cautious reading)."""
        return [c for c in self.repository.active() if self._cluster_match(c, uid) != "other"]

    # -- reads -------------------------------------------------------------------------------------------------------

    def status(self, recommendation_id: str) -> dict[str, Any]:
        """What was applied for this recommendation, where its rollout and analysis stand, and what can be done next.

        Asking advances the persisted record: the Deployments are read, the rollout state is stored, and a completed rollout
        whose analysis is pending is analysed. It reads the cluster and writes this service's own tables; it never patches.
        """
        change = self.repository.latest_for(recommendation_id)
        approval = self.approvals.get_by_recommendation_id(recommendation_id)
        approved = approval is not None and approval.status == ApprovalStatus.APPROVED and self._trusted(approval)
        lost = None
        uid = self._connected_uid()
        match = self._cluster_match(change, uid)
        if change is not None and change.status in (APPLIED, RESET):
            change, lost = self._advance(change)
        active = self._active_here(uid)
        blocking = next((c for c in active if change is None or c.application_id != change.application_id), None)
        rollout = change.rollout if change else None
        analyses = self._analyses_of(change)
        current = analyses.get((rollout or {}).get("direction") or (operation_view.direction_of(change) if change else ""))
        resetting = change is not None and change.status == RESET and (rollout or {}).get("state") == rollout_watch.ROLLING_OUT
        can_apply = bool(approved and not active and self.writes_enabled and not resetting)
        state = (rollout or {}).get("state")
        return {
            "recommendationId": recommendation_id, "application": change.to_dict() if change else None,
            # Kept for the callers that only ask "is it settled": True only when the Deployments say so, never from a patch.
            "rolloutComplete": (None if change is None or change.status not in (APPLIED, RESET) or lost is not None or state is None
                                else state == rollout_watch.COMPLETED),
            "rollout": rollout, "analysis": {key: value.to_dict() for key, value in analyses.items()},
            "operation": operation_view.build(change, rollout, current, approved=approved, connection=lost,
                                              interrupted=bool(change and change.status == APPLYING and not _MUTATION.locked())),
            "states": self._distinct_states(change, rollout, analyses),
            "experiments": self._experiments(recommendation_id),
            "canApply": can_apply,
            "writesEnabled": self.writes_enabled,
            "canReset": bool(change and change.status in RESETTABLE and match != "other"),
            # "same" | "other" | "unknown" | None: the stored change against the cluster connected now; "other" is shown, never reset here.
            "clusterMatch": match,
            "changeCluster": ({"context": change.cluster.get("context"), "apiServer": change.cluster.get("apiServer")}
                              if change is not None and match == "other" else None),
            "canRetryAnalysis": bool(change and state == rollout_watch.COMPLETED and current is not None
                                     and current.status == ANALYSIS_FAILED),
            "reason": (None if can_apply else
                       WRITES_OFF if not self.writes_enabled else
                       f"{blocking.recommendation_id} is still applied; reset it first." if blocking else
                       "The reset is still rolling out; apply again when it has settled." if resetting else
                       "Only an approved recommendation can be applied."),
            "tier": self.tier,
        }

    @staticmethod
    def _distinct_states(change: AppliedChange | None, rollout: dict[str, Any] | None,
                         analyses: Mapping[str, ChangeAnalysis]) -> dict[str, Any]:
        """Four different questions, answered separately; none of them implies the next."""
        direction = (rollout or {}).get("direction")
        analysis = analyses.get(direction) if direction else None
        return {
            "patchesAccepted": None if change is None else change.status in (APPLIED, RESET, RESET_FAILED),
            "rolloutCompleted": None if rollout is None else rollout.get("state") == rollout_watch.COMPLETED,
            "analysisCompleted": None if analysis is None else analysis.status == ANALYSIS_COMPLETED,
            # Nothing in Apply, Reset or the analysis injects a failure, so none of this is an experiment.
            "experimentallyValidated": False,
            "experimentNote": "No controlled experiment is run by Apply, Reset or the analysis. The analysis recalculates risk "
                              "from observed pod placement; experimental validation is a separate, explicit run.",
        }

    def _experiments(self, recommendation_id: str) -> dict[str, Any]:
        """Controlled-experiment measurements for this recommendation (real cluster, completed), or none. Never a recalculation."""
        note = ("Only a completed controlled experiment on a real cluster is listed here. The recalculation of the applied change "
                "is not an experiment and is shown separately.")
        if self.experiments is None:
            return {"available": False, "measurements": [], "note": note}
        try:
            records = [r for r in self.experiments.list_measurements() if r.recommendation_id == recommendation_id]
        except Exception:      # a store that cannot be read is unknown, not "none were run"
            return {"available": False, "measurements": [], "note": note}
        return {"available": True, "note": note, "measurements": [{
            "experimentId": r.experiment_id, "status": r.status, "validated": r.validated, "predictionError": r.prediction_error,
            "workflowBasis": (r.provenance or {}).get("workflowBasis") or "FROZEN",
            "finishedAt": r.finished_at.isoformat() if r.finished_at else None} for r in records]}

    def _analyses_of(self, change: AppliedChange | None) -> dict[str, ChangeAnalysis]:
        if change is None or self.analyses is None:
            return {}
        found = {direction: self.analyses.latest_for(change.application_id, direction)
                 for direction in (DIRECTION_APPLY, DIRECTION_RESET)}
        return {direction: analysis for direction, analysis in found.items() if analysis is not None}

    def _require_armed(self) -> None:
        if not self.writes_enabled:
            raise ApplicationRefused("CLUSTER_WRITES_NOT_ENABLED", WRITES_OFF, http_status=403)

    # -- watching the rollout, then analysing it -----------------------------------------------------------------------

    def _advance(self, change: AppliedChange) -> tuple[AppliedChange, dict[str, str] | None]:
        """Read the Deployments once, store where the rollout stands, and analyse it if it has just completed.

        Returns the stored change and, when the cluster could not be read just now, why (a coded cluster error: unreachable, the
        credential rejected, not allowed). That is about this read only: the stored state is left as it was, never turned into a
        failure or a success.
        """
        lost: dict[str, str] | None = None
        record = change.rollout
        if record is not None and record.get("state") not in rollout_watch.TERMINAL:
            with _ADVANCE:
                current = self.repository.get(change.application_id) or change
                record = current.rollout
                if record is not None and record.get("state") not in rollout_watch.TERMINAL:
                    try:
                        gateway = self._gateway_factory()
                        updated, event = rollout_watch.observe(record, gateway)
                        if updated.get("state") == rollout_watch.COMPLETED and record.get("direction") == DIRECTION_RESET:
                            updated["restoration"] = self._restoration(gateway, current)
                    except _CLUSTER_ERRORS as error:
                        problem = _cluster_error(error)
                        lost = {"code": problem.code, "message": problem.message}
                    else:
                        events = [self._event(event, record.get("direction") or "")] if event else []
                        change = (self.repository.advance_rollout(change.application_id, record.get("since"), updated, events)
                                  or self.repository.get(change.application_id) or change)
        if lost is None:
            change = self._analyse_if_due(change)
        return change, lost

    def _restoration(self, gateway: ApplyGateway, change: AppliedChange) -> dict[str, Any]:
        """Whether the Deployments now hold the stored fields again: replicas, spread constraints and anti-affinity only."""
        live = gateway.read_deployments(change.namespace)
        differences = []
        for snapshot in change.before:
            name, now = snapshot["name"], live.get(snapshot["name"])
            if now is None:
                differences.append(f"{name}: no longer in the cluster")
                continue
            if snapshot.get("uid") and now.uid and snapshot["uid"] != now.uid:
                differences.append(f"{name}: is not the Deployment that was changed")
            if now.replicas != snapshot["replicas"]:
                differences.append(f"{name}: {now.replicas} replicas, stored {snapshot['replicas']}")
            if list(now.topology_spread_constraints) != list(snapshot["topology_spread_constraints"] or []):
                differences.append(f"{name}: topology spread constraints differ from the stored ones")
            if (now.pod_anti_affinity or None) != (snapshot["pod_anti_affinity"] or None):
                differences.append(f"{name}: pod anti-affinity differs from the stored one")
        return {"verified": not differences, "differences": differences, "checkedAt": _now().isoformat()}

    def _analyse_if_due(self, change: AppliedChange) -> AppliedChange:
        record = change.rollout
        if self.analyses is None or self.analyser is None or record is None:
            return change
        if record.get("state") != rollout_watch.COMPLETED:
            return change
        analysis = self.analyses.latest_for(change.application_id, record.get("direction") or DIRECTION_APPLY)
        if analysis is None or analysis.status != ANALYSIS_PENDING:
            return change
        self._run_analysis(change, analysis)
        return self.repository.get(change.application_id) or change

    def _run_analysis(self, change: AppliedChange, analysis: ChangeAnalysis) -> None:
        """One recalculation at a time per analysis; the outcome (or the reason there is none) is stored on its row."""
        with _ANALYSING_GUARD:
            if analysis.analysis_id in _ANALYSING:
                return
            _ANALYSING.add(analysis.analysis_id)
        try:
            analysis = self.analyses.latest_for(change.application_id, analysis.direction) or analysis
            if analysis.status != ANALYSIS_PENDING:
                return
            outcome = self._recalculate(change, analysis)
            self.analyses.update(outcome)
            self.repository.append_events(change.application_id, [self._event(f"analysis-{outcome.status.lower()}", outcome.direction)])
        finally:
            with _ANALYSING_GUARD:
                _ANALYSING.discard(analysis.analysis_id)

    def _recalculate(self, change: AppliedChange, analysis: ChangeAnalysis) -> ChangeAnalysis:
        try:
            gateway = self._gateway_factory()
            uid = getattr(gateway.identity(), "cluster_uid", None)
            if not uid:
                raise EvidenceError("CLUSTER_IDENTITY_UNKNOWN", "The connected cluster could not be identified just now, so its "
                                    "topology was not compared with the baseline. Try again in a moment.",
                                    http_status=503, component=COMPONENT, retryable=True)
            if uid != change.cluster.get("clusterUid"):
                raise AnalysisUnavailable("CLUSTER_CHANGED", "The connected cluster is not the one this change was applied to, "
                                          "so its topology cannot be compared with the baseline taken from the other one.")
            observed = self.analyser.recalculate(analysis.basis)
            completed_at = (change.rollout or {}).get("completedAt")
            captured = observed.get("topologyCapturedAt")
            observed["capturedAfterRollout"] = _not_before(captured, completed_at)
            if observed["capturedAfterRollout"] is False:
                raise EvidenceError("TOPOLOGY_NOT_FRESH", "The topology was captured before the rollout was seen to complete, "
                                    "so it is not post-change evidence.", http_status=409, component=COMPONENT, retryable=True)
            analysis.observed = observed
            analysis.comparison = compare(analysis.baseline, analysis.predicted, observed)
            if analysis.direction == DIRECTION_RESET:
                analysis.comparison["restoration"] = (change.rollout or {}).get("restoration")
            analysis.status, analysis.error, analysis.completed_at = ANALYSIS_COMPLETED, None, _now()
        except AnalysisUnavailable as error:
            analysis.status, analysis.error = ANALYSIS_UNAVAILABLE, error.detail()
        except _CLUSTER_ERRORS as error:
            analysis.status, analysis.error = ANALYSIS_FAILED, _cluster_error(error).detail()
        except EvidenceError as error:
            analysis.status, analysis.error = ANALYSIS_FAILED, error.detail()
        except Exception:       # never leak raw exception text; the change itself is untouched and the analysis can be retried
            analysis.status = ANALYSIS_FAILED
            analysis.error = {"code": "ANALYSIS_ERROR", "message": "The failure-impact recalculation failed unexpectedly.",
                              "component": COMPONENT, "retryable": True}
        return analysis

    def retry_analysis(self, recommendation_id: str) -> AppliedChange:
        """Run a failed recalculation again. It reads the cluster and recalculates; it applies and resets nothing."""
        change = self.repository.latest_for(recommendation_id)
        if change is None or self.analyses is None or self.analyser is None:
            raise ApplicationRefused("NOTHING_TO_ANALYSE", "No applied change for this recommendation has an analysis to retry.")
        change, lost = self._advance(change)
        record = change.rollout or {}
        if lost:
            raise ApplicationRefused(lost["code"], lost["message"], http_status=502, retryable=lost["code"] != "CLUSTER_AUTH_FAILED")
        if record.get("state") != rollout_watch.COMPLETED:
            raise ApplicationRefused("ROLLOUT_NOT_COMPLETE", "The rollout has not completed, so there is nothing to analyse yet.")
        analysis = self.analyses.latest_for(change.application_id, record.get("direction") or DIRECTION_APPLY)
        if analysis is None or analysis.status != ANALYSIS_FAILED:
            raise ApplicationRefused("ANALYSIS_NOT_RETRYABLE", "Only an analysis that failed can be retried; a completed one is "
                                     "kept, and an unavailable one has no comparable basis.")
        analysis.status, analysis.error = ANALYSIS_PENDING, None
        self.analyses.update(analysis)
        self.repository.append_events(change.application_id, [self._event("analysis-retried", analysis.direction)])
        self._run_analysis(change, analysis)
        return self.repository.get(change.application_id) or change

    def _require_settled_reset(self, uid: str | None) -> None:
        """A reset whose rollout is still going is not finished, so nothing new is applied over it (it is read now, not trusted)."""
        last = self.repository.latest()
        if last is None or last.status != RESET or not last.rollout or self._cluster_match(last, uid) == "other":
            return
        if last.rollout.get("state") in rollout_watch.TERMINAL:
            return
        last, lost = self._advance(last)
        if lost is None and (last.rollout or {}).get("state") == rollout_watch.ROLLING_OUT:
            raise ApplicationRefused("RESET_ROLLOUT_IN_PROGRESS", f"The reset of {last.recommendation_id} is still rolling out. "
                                     f"Apply again when it has settled.", retryable=True)

    # -- apply -------------------------------------------------------------------------------------------------------

    def apply(self, recommendation_id: str, applied_by: str, *,
              evaluate: Callable[[ApprovalModel], LiveRecommendation]) -> AppliedChange:
        self._require_armed()
        who = _named(applied_by)
        with _MUTATION:         # one Apply or Reset at a time: two clicks, two tabs or two requests cannot both pass the checks
            return self._apply_locked(recommendation_id, who, evaluate)

    def _apply_locked(self, recommendation_id: str, who: str,
                      evaluate: Callable[[ApprovalModel], LiveRecommendation]) -> AppliedChange:
        approval = self.approvals.get_by_recommendation_id(recommendation_id)
        if approval is None or approval.status != ApprovalStatus.APPROVED:
            raise ApplicationRefused("NOT_APPROVED", "Only an approved recommendation can be applied.")
        if not self._trusted(approval):
            raise ApplicationRefused("PROVENANCE_NOT_ACCEPTED", f"This approval was not made at the {self.tier} gate, so it "
                                     f"cannot be applied here.")
        uid = self._connected_uid()
        self._require_settled_reset(uid)
        if active := self._active_here(uid):
            raise ApplicationRefused("APPLICATION_ACTIVE", f"{active[0].recommendation_id} is still applied to the cluster. "
                                     f"Reset it before applying another change.")
        yaml_text, namespace, current = self._still_what_was_approved(approval, recommendation_id, evaluate)

        try:
            gateway = self._gateway_factory()
            identity = gateway.identity()
            if not getattr(identity, "cluster_uid", None):
                raise ApplicationRefused("CLUSTER_IDENTITY_UNKNOWN", "The connected cluster's identity (kube-system UID) could "
                                         "not be read, so a change could not be reset safely. Nothing was applied.")
            live = gateway.read_deployments(namespace)
            try:
                patches = check_proposal(yaml_text, live, namespace=namespace,
                                         baseline_versions={name: d.resource_version for name, d in live.items()},
                                         max_added_replicas=self._max_added)
            except ValidationRefusedError as error:
                raise ApplicationRefused(error.code, error.message, http_status=422) from error
            cluster = {"context": identity.context, "apiServer": identity.api_server, "clusterUid": identity.cluster_uid}
            planned = None
            if self.analyses is not None and self.analyser is not None:
                try:       # the baseline and the prediction are fixed from the evaluation just re-verified, before any write
                    planned = self.analyser.plan(_Planning(recommendation_id, namespace, [p.name for p in patches], cluster), current)
                except AnalysisUnavailable as error:
                    raise ApplicationRefused(error.code, error.message, http_status=422) from error
            change = self.repository.save(AppliedChange(
                application_id=f"apply-{uuid.uuid4().hex[:16]}", recommendation_id=recommendation_id, approval_id=approval.id,
                tier=self.tier, namespace=namespace, status=APPLYING, yaml_applied=yaml_text, yaml_sha256=_sha256(yaml_text),
                before=[_snapshot(live[patch.name]) for patch in patches], cluster=cluster,
                applied_by=who, applied_at=_now(),
                events=[self._event("approval-verified", f"{recommendation_id} against the evaluation made now"),
                        self._event("snapshot-saved", ", ".join(patch.name for patch in patches)),
                        self._event("applying", f"by {who}")]))
            if planned is not None:
                self.analyses.planned(change, {**planned, "basis": {**planned["basis"], "cluster": cluster,
                                                                     "applicationId": change.application_id}}, DIRECTION_APPLY)
            patched: list[str] = []
            try:
                for patch in patches:
                    # A request that fails by timeout may still have been applied, so the one that failed is put back too;
                    # only a 409 is known to have changed nothing (and a restore would clobber the edit that caused it).
                    attempting = patch.name
                    patched.append(attempting)
                    gateway.patch_deployment(patch.document, expect_resource_version=live[patch.name].resource_version)
                    change.events.append(self._event("patched", patch.name))
                    change = self.repository.update(change)       # persisted as it happens, so a status request can show it
            except _CLUSTER_ERRORS as error:
                failure = _cluster_error(error)
                if isinstance(error, ConflictError):
                    patched.remove(attempting)
                self._put_back_after_failed_apply(gateway, change, patched, failure)
                raise failure from error
            change.status = APPLIED
            change.events.append(self._event("applied", ", ".join(patched)))
            change.rollout = rollout_watch.fresh(DIRECTION_APPLY, namespace, patched,
                                                 uids={s["name"]: s.get("uid") for s in change.before})
            return self.repository.update(change)
        except _CLUSTER_ERRORS as error:
            raise _cluster_error(error) from error

    def _still_what_was_approved(self, approval: ApprovalModel, recommendation_id: str,
                                 evaluate: Callable[[ApprovalModel], LiveRecommendation]) -> tuple[str, str, LiveRecommendation]:
        current = evaluate(approval)
        standing = evaluate_eligibility(current, recommendation_id)
        snapshot = approval.evaluation_snapshot or {}
        bound = (snapshot.get("approvalCheck") or {}).get("proposalFingerprint")
        if not standing.eligible or bound is None or bound != standing.fingerprint:
            raise ApplicationRefused("APPROVAL_STALE", "The topology or the predicted effect changed since this was approved "
                                     "(" + (standing.reason or "the fingerprint differs") + "), so it is not what was approved. "
                                     "Request approval again.")
        recorded = (snapshot.get("proposal") or {}).get("yaml") or {}
        proposal = build_yaml_proposal(current, recommendation_id)
        if (recorded.get("origin") != "server-generated" or proposal.status != GENERATED
                or (self._require_applicable and not proposal.applicable)
                or not approval.yaml_snapshot or proposal.sha256 != recorded.get("sha256")
                or _sha256(approval.yaml_snapshot) != recorded.get("sha256")):
            raise ApplicationRefused("YAML_NOT_CURRENT", "The YAML the server generates now is not the YAML that was approved "
                                     "(or it was not server-generated), so nothing is applied.")
        namespaces = {patch.namespace for patch in proposal.patches}
        if len(namespaces) != 1:
            raise ApplicationRefused("YAML_NAMESPACE", "The change must touch exactly one namespace.", http_status=422)
        return approval.yaml_snapshot, namespaces.pop(), current

    def _put_back_after_failed_apply(self, gateway: ApplyGateway, change: AppliedChange, patched: list[str],
                                     failure: ApplicationRefused) -> None:
        change.detail = {"failed": failure.code, "message": failure.message, "patched": list(patched)}
        change.events.append(self._event("apply-failed", failure.code))
        before = {snapshot["name"]: snapshot for snapshot in change.before}
        try:
            for name in reversed(patched):
                gateway.restore_deployment(_restorable(before[name]))
                change.events.append(self._event("restored", name))
            change.status = FAILED
            self._close_analysis(change, DIRECTION_APPLY, "APPLY_FAILED", "The change was not applied (it was put back), so "
                                 "there is no post-change topology to compare.")
        except _CLUSTER_ERRORS:
            change.status = RESET_FAILED
            change.events.append(self._event("restore-failed", "Reset it to try again"))
        self.repository.update(change)

    def _close_analysis(self, change: AppliedChange, direction: str, code: str, message: str) -> None:
        """A pending analysis that will now never run says why, instead of staying pending for ever."""
        if self.analyses is None:
            return
        analysis = self.analyses.latest_for(change.application_id, direction)
        if analysis is not None and analysis.status == ANALYSIS_PENDING:
            analysis.status = ANALYSIS_UNAVAILABLE
            analysis.error = {"code": code, "message": message, "component": COMPONENT, "retryable": False}
            self.analyses.update(analysis)

    # -- reset -------------------------------------------------------------------------------------------------------

    def reset(self, recommendation_id: str, reset_by: str) -> AppliedChange:
        self._require_armed()
        who = _named(reset_by)
        with _MUTATION:
            return self._reset_locked(recommendation_id, who)

    def _reset_locked(self, recommendation_id: str, who: str) -> AppliedChange:
        change = self.repository.latest_for(recommendation_id)
        if change is None or change.status not in RESETTABLE:
            raise ApplicationRefused("NOTHING_TO_RESET", "Nothing applied for this recommendation is waiting to be reset.")
        try:
            gateway = self._gateway_factory()
            identity = gateway.identity()
            if not getattr(identity, "cluster_uid", None):
                raise ApplicationRefused("CLUSTER_IDENTITY_UNKNOWN", "The connected cluster could not be identified just now "
                                         "(its connection may still be renewing), so nothing was restored. Try again in a moment.",
                                         http_status=503, retryable=True)
            if identity.cluster_uid != change.cluster.get("clusterUid"):
                raise ApplicationRefused("CLUSTER_CHANGED", "The connected cluster is not the one this change was applied to, so "
                                         "its stored state is not restored here. Reconnect that cluster to reset it.")
            self._require_same_deployments(gateway, change)
            for snapshot in reversed(change.before):
                gateway.restore_deployment(_restorable(snapshot))
                change.events.append(self._event("restored", snapshot["name"]))
        except _CLUSTER_ERRORS as error:
            failure = _cluster_error(error)
            change.status = RESET_FAILED
            change.detail = {"failed": failure.code, "message": failure.message}
            change.events.append(self._event("reset-failed", failure.code))
            self.repository.update(change)
            raise failure from error
        change.status, change.reset_by, change.reset_at, change.detail = RESET, who, _now(), None
        change.events.append(self._event("reset", f"by {who}"))
        change.rollout = rollout_watch.fresh(DIRECTION_RESET, change.namespace, change.deployments,
                                             uids={s["name"]: s.get("uid") for s in change.before})
        stored = self.repository.update(change)
        self._plan_reset_analysis(stored)
        return stored

    def _require_same_deployments(self, gateway: ApplyGateway, change: AppliedChange) -> None:
        """Refuse to put stored fields onto a Deployment that is not the one they came from (deleted and recreated, or gone)."""
        live = gateway.read_deployments(change.namespace)
        for snapshot in change.before:
            now = live.get(snapshot["name"])
            if now is None:
                raise ApplicationRefused("DEPLOYMENT_MISSING", f"Deployment {snapshot['name']} is no longer in the cluster, so "
                                         f"its stored state cannot be restored.")
            if snapshot.get("uid") and now.uid and snapshot["uid"] != now.uid:
                raise ApplicationRefused("DEPLOYMENT_REPLACED", f"Deployment {snapshot['name']} was deleted and created again "
                                         f"since the change, so the stored state is not restored onto the new one.")

    def _plan_reset_analysis(self, change: AppliedChange) -> None:
        """The reset gets its own analysis, compared with the apply's immutable baseline; the apply's pending one is closed."""
        if self.analyses is None or self.analyser is None:
            return
        self._close_analysis(change, DIRECTION_APPLY, "SUPERSEDED_BY_RESET",
                             "The change was reset before its rollout was analysed, so no post-change recalculation exists for it.")
        prior = self.analyses.latest_for(change.application_id, DIRECTION_APPLY)
        if prior is not None:
            self.analyses.planned(change, plan_reset(prior), DIRECTION_RESET)

    @staticmethod
    def _event(event: str, detail: str) -> dict[str, str]:
        return {"at": _now().isoformat(), "event": event, "detail": detail}


class _Planning:
    """What ``ChangeAnalyser.plan`` needs to know of the change being applied, before its record exists."""

    def __init__(self, recommendation_id: str, namespace: str, deployments: list[str], cluster: dict[str, Any]) -> None:
        self.recommendation_id, self.namespace = recommendation_id, namespace
        self.application_id, self.deployments, self.cluster = None, deployments, cluster


def _not_before(captured: str | None, completed: str | None) -> bool | None:
    """Whether the topology was captured at or after the moment the rollout was seen to complete (None: a time is unknown)."""
    try:
        return datetime.fromisoformat(str(captured).replace("Z", "+00:00")) >= datetime.fromisoformat(str(completed))
    except (TypeError, ValueError):
        return None
