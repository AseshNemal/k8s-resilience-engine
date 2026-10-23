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
"""

import hashlib
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Protocol

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
RESTORED_FIELDS = ("name", "namespace", "resource_version", "generation", "replicas", "selector", "pod_labels",
                   "topology_spread_constraints", "pod_anti_affinity")


class ApplyGateway(Protocol):
    """What Apply and Reset need of the cluster. ``KubernetesTestClusterGateway(connected_cluster=True, allow_writes=True)``."""

    def identity(self) -> Any: ...
    def read_deployments(self, namespace: str) -> Mapping[str, ObservedDeployment]: ...
    def patch_deployment(self, document: dict[str, Any], *, expect_resource_version: str) -> None: ...
    def restore_deployment(self, snapshot: Any) -> None: ...
    def rollout_complete(self, namespace: str, names: Sequence[str]) -> bool: ...


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
                 trusted: Callable[[ApprovalModel], bool], require_applicable: bool = True, max_added_replicas: int = 5) -> None:
        # ``require_applicable`` mirrors the approval service's own hook: a DECLARED workflow's YAML is never "applicable"
        # at the production gate, and the research tier (which approved it, knowing that) turns the requirement off.
        self.approvals, self.repository, self._gateway_factory = approvals, repository, gateway_factory
        self.tier, self._trusted, self._max_added = tier, trusted, max_added_replicas
        self._require_applicable = require_applicable

    # -- reads -------------------------------------------------------------------------------------------------------

    def status(self, recommendation_id: str) -> dict[str, Any]:
        """What was applied for this recommendation, whether it is settled in the cluster, and what can be done next."""
        change = self.repository.latest_for(recommendation_id)
        approval = self.approvals.get_by_recommendation_id(recommendation_id)
        approved = approval is not None and approval.status == ApprovalStatus.APPROVED and self._trusted(approval)
        active = self.repository.active()
        blocking = next((c for c in active if change is None or c.application_id != change.application_id), None)
        rollout = None
        if change is not None and change.status in (APPLIED, RESET):
            rollout = self._rollout(change)
        return {
            "recommendationId": recommendation_id, "application": change.to_dict() if change else None,
            "rolloutComplete": rollout,
            "canApply": bool(approved and not active),
            "canReset": bool(change and change.status in RESETTABLE),
            "reason": (None if approved and not active else
                       f"{blocking.recommendation_id} is still applied; reset it first." if blocking else
                       "Only an approved recommendation can be applied."),
            "tier": self.tier,
        }

    def _rollout(self, change: AppliedChange) -> bool | None:
        try:
            return bool(self._gateway_factory().rollout_complete(change.namespace, change.deployments))
        except _CLUSTER_ERRORS:
            return None      # unknown, not "complete"

    # -- apply -------------------------------------------------------------------------------------------------------

    def apply(self, recommendation_id: str, applied_by: str, *,
              evaluate: Callable[[ApprovalModel], LiveRecommendation]) -> AppliedChange:
        who = _named(applied_by)
        approval = self.approvals.get_by_recommendation_id(recommendation_id)
        if approval is None or approval.status != ApprovalStatus.APPROVED:
            raise ApplicationRefused("NOT_APPROVED", "Only an approved recommendation can be applied.")
        if not self._trusted(approval):
            raise ApplicationRefused("PROVENANCE_NOT_ACCEPTED", f"This approval was not made at the {self.tier} gate, so it "
                                     f"cannot be applied here.")
        if active := self.repository.active():
            raise ApplicationRefused("APPLICATION_ACTIVE", f"{active[0].recommendation_id} is still applied to the cluster. "
                                     f"Reset it before applying another change.")
        yaml_text, namespace = self._still_what_was_approved(approval, recommendation_id, evaluate)

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
            change = self.repository.save(AppliedChange(
                application_id=f"apply-{uuid.uuid4().hex[:16]}", recommendation_id=recommendation_id, approval_id=approval.id,
                tier=self.tier, namespace=namespace, status=APPLYING, yaml_applied=yaml_text, yaml_sha256=_sha256(yaml_text),
                before=[_snapshot(live[patch.name]) for patch in patches],
                cluster={"context": identity.context, "apiServer": identity.api_server, "clusterUid": identity.cluster_uid},
                applied_by=who, applied_at=_now(), events=[self._event("applying", f"by {who}")]))
            patched: list[str] = []
            try:
                for patch in patches:
                    # A request that fails by timeout may still have been applied, so the one that failed is put back too;
                    # only a 409 is known to have changed nothing (and a restore would clobber the edit that caused it).
                    attempting = patch.name
                    patched.append(attempting)
                    gateway.patch_deployment(patch.document, expect_resource_version=live[patch.name].resource_version)
                    change.events.append(self._event("patched", patch.name))
            except _CLUSTER_ERRORS as error:
                failure = _cluster_error(error)
                if isinstance(error, ConflictError):
                    patched.remove(attempting)
                self._put_back_after_failed_apply(gateway, change, patched, failure)
                raise failure from error
            change.status = APPLIED
            change.events.append(self._event("applied", ", ".join(patched)))
            return self.repository.update(change)
        except _CLUSTER_ERRORS as error:
            raise _cluster_error(error) from error

    def _still_what_was_approved(self, approval: ApprovalModel, recommendation_id: str,
                                 evaluate: Callable[[ApprovalModel], LiveRecommendation]) -> tuple[str, str]:
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
        return approval.yaml_snapshot, namespaces.pop()

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
        except _CLUSTER_ERRORS:
            change.status = RESET_FAILED
            change.events.append(self._event("restore-failed", "Reset it to try again"))
        self.repository.update(change)

    # -- reset -------------------------------------------------------------------------------------------------------

    def reset(self, recommendation_id: str, reset_by: str) -> AppliedChange:
        who = _named(reset_by)
        change = self.repository.latest_for(recommendation_id)
        if change is None or change.status not in RESETTABLE:
            raise ApplicationRefused("NOTHING_TO_RESET", "Nothing applied for this recommendation is waiting to be reset.")
        try:
            gateway = self._gateway_factory()
            identity = gateway.identity()
            if getattr(identity, "cluster_uid", None) != change.cluster.get("clusterUid"):
                raise ApplicationRefused("CLUSTER_CHANGED", "The connected cluster is not the one this change was applied to, so "
                                         "its stored state is not restored here. Reconnect that cluster to reset it.")
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
        return self.repository.update(change)

    @staticmethod
    def _event(event: str, detail: str) -> dict[str, str]:
        return {"at": _now().isoformat(), "event": event, "detail": detail}
