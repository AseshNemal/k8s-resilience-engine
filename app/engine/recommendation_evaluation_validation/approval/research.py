"""The research approval tier: approving a recommendation computed from a workflow a person DECLARED, for research validation only.

Why it exists. The production gate (``ApprovalService``) trusts one thing: a recommendation computed on live evidence from a
workflow Component 1 observed, approved and froze. Component 1 has nothing to freeze while an application emits no traces, so
nothing can be approved and a validation on a disposable test cluster cannot start. The developer decided Member 4 should
have its own path for that, without touching Component 1: this one.

What it is, and what it is not. It is a second, separately named tier. It does not widen the production gate: the production service,
its routes and its tests are unchanged, ``has_verified_provenance`` still accepts live evidence only, and a record made here is
refused by the production ``approve`` (provenance unverified). Everything the production gate checks is checked here too
(verified feasible, ranked, bound to the exact plan and predicted effect, server-generated YAML regenerated at decision time). What
differs is exactly four things:

* the evidence must be a DECLARED workflow evaluation (``DECLARED_WORKFLOW_SOURCE``), never a live one (a live one goes through the
  production gate);
* the request carries a named person's written acknowledgement that the workflow is declared, not observed, and the record keeps
  it, with the declaration's digest and the scope below;
* because the declaration is only a file, the approval is bound to its digest: the evaluation is made again when the request is
  decided, and a declaration that changed since makes the approval stale;
* the YAML may be marked "not applicable" (it always is for declared evidence); that flag exists to keep it from being applied to the
  connected cluster, and approval applies nothing in either tier.

Scope. A research approval is honoured only by ``ValidationPlan.from_approval(..., allow_declared_workflow=True)``, which in turn only
runs against a designated disposable test cluster, and every experiment record made from it says its workflow was declared. Whatever
such an experiment shows is conditional on the declaration being right.
"""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.engine.recommendation_evaluation_validation.approval.models import (
    DECLARED_WORKFLOW_SOURCE,
    ApprovalModel,
    LiveRecommendation,
)
from app.engine.recommendation_evaluation_validation.approval.service import (
    SERVER_GENERATED,
    ApprovalService,
    PreconditionError,
    StaleProposalError,
)
from app.shared.evidence_errors import EvidenceError

RESEARCH_TIER = "research-declared-workflow"
MIN_ACKNOWLEDGEMENT_CHARS = 20
ANONYMOUS = "developer"      # what the production routes record when nobody is named; a research decision needs a person
RESEARCH_SCOPE = (
    "Research validation on a designated disposable test cluster only. The workflow was declared by a person, not observed from "
    "traces and not approved or frozen by Component 1, so every result is conditional on the declaration being correct."
)


class ResearchProvenanceError(EvidenceError):
    """The approval is not a research-tier approval, so this gate does not honour it."""

    def __init__(self, reason: str) -> None:
        super().__init__("APPROVAL_RESEARCH_PROVENANCE_MISSING",
                         f"This is not a research-tier approval of a declared workflow ({reason}).",
                         http_status=409, component="recommendation")


def _provenance(approval: ApprovalModel) -> dict[str, Any]:
    return (approval.evaluation_snapshot or {}).get("provenance") or {}


def declaration_digest(evidence: dict[str, Any] | None) -> str | None:
    digest = ((evidence or {}).get("declaredWorkflow") or {}).get("declarationSha256")
    return digest if isinstance(digest, str) and len(digest) == 64 else None


def research_acknowledgement(approval: ApprovalModel) -> dict[str, Any] | None:
    ack = _provenance(approval).get("researchAcknowledgement")
    return ack if isinstance(ack, dict) else None


def _why_not_research(approval: ApprovalModel) -> str | None:
    """None when ``approval`` is a complete research-tier approval; otherwise what is missing."""
    provenance = _provenance(approval)
    if provenance.get("source") != DECLARED_WORKFLOW_SOURCE:
        return "its evidence is not a declared-workflow evaluation"
    if provenance.get("researchTier") != RESEARCH_TIER:
        return "it was not requested through the research tier"
    ack = research_acknowledgement(approval) or {}
    if not str(ack.get("by") or "").strip() or not str(ack.get("statement") or "").strip():
        return "it carries no named acknowledgement"
    if declaration_digest(provenance) is None:
        return "it does not record which declaration it was computed from"
    if (((approval.evaluation_snapshot or {}).get("proposal") or {}).get("yaml") or {}).get("origin") != SERVER_GENERATED:
        return "its YAML was not generated by the server"
    return None


def has_research_provenance(approval: ApprovalModel) -> bool:
    return _why_not_research(approval) is None


class ResearchApprovalService(ApprovalService):
    """The production gate's checks, with the four differences the module docstring lists, and nothing else relaxed."""

    require_applicable_yaml = False

    def _require_provenance(self, approval: ApprovalModel) -> None:
        reason = _why_not_research(approval)
        if reason is not None:
            raise ResearchProvenanceError(reason)

    def request_research_approval(
        self, recommendation_id: str, proposal_version: str, live: LiveRecommendation, *,
        acknowledged_by: str, acknowledgement: str, proposal_yaml: str | None,
    ) -> ApprovalModel:
        if (live.evidence or {}).get("source") != DECLARED_WORKFLOW_SOURCE:
            raise PreconditionError("The research tier is only for recommendations computed from a declared workflow; one computed "
                                    "from a frozen workflow goes through the normal approval.")
        who, statement = (acknowledged_by or "").strip(), (acknowledgement or "").strip()
        if not who or who.lower() == ANONYMOUS:
            raise PreconditionError("A research approval needs the name of the person acknowledging it.")
        if len(statement) < MIN_ACKNOWLEDGEMENT_CHARS:
            raise PreconditionError(f"Say in at least {MIN_ACKNOWLEDGEMENT_CHARS} characters that you understand the workflow is "
                                    f"declared, not observed, and what you are approving it for.")
        if declaration_digest(live.evidence) is None:
            raise PreconditionError("The evaluation does not record which declaration it came from, so it cannot be approved.")
        if proposal_yaml is None:
            raise PreconditionError("The server generated no YAML for this candidate, so there is nothing to approve.")
        # Recommendation ids come from the workflow, service and type, so a production record can share one with this request. The
        # latest record for an id is the one a gate sees, so a research request must not shadow a production one: it refuses.
        existing = self.repository.get_by_recommendation_id(recommendation_id)
        if existing is not None and not has_research_provenance(existing):
            raise PreconditionError(f"{recommendation_id} already has an approval record that is not a research-tier one; the tiers are "
                                    f"kept apart, so this request would hide it. Decide that record first.")
        evidence = {**live.evidence, "researchTier": RESEARCH_TIER, "researchAcknowledgement": {
            "by": who, "statement": statement, "at": datetime.now(timezone.utc).isoformat(), "scope": RESEARCH_SCOPE}}
        return self.request_approval(recommendation_id, proposal_version, live.report, requested_by=who,
                                     evidence=evidence, proposal_yaml=proposal_yaml)

    def reject(self, recommendation_id: str, expected_proposal_version: str, note: str,
               decided_by: str = ANONYMOUS) -> ApprovalModel:
        """Reject a research request. Only a research-tier record can be decided here, and only by a named person."""
        who = (decided_by or "").strip()
        if not who or who.lower() == ANONYMOUS:
            raise PreconditionError("A research decision is made by a named person.")
        existing = self.repository.get_by_recommendation_id(recommendation_id)
        if existing is not None:
            self._require_provenance(existing)      # a production record is not this gate's to decide
        return super().reject(recommendation_id, expected_proposal_version, note, decided_by=who)

    def approve(
        self, recommendation_id: str, expected_proposal_version: str, note: str, decided_by: str = ANONYMOUS,
        evaluate: Callable[[ApprovalModel], LiveRecommendation] | None = None,
    ) -> ApprovalModel:
        who = (decided_by or "").strip()
        if not who or who.lower() == ANONYMOUS:
            raise PreconditionError("A research approval is decided by a named person.")

        def current(approval: ApprovalModel) -> LiveRecommendation:
            live = evaluate(approval)
            if (live.evidence or {}).get("source") != DECLARED_WORKFLOW_SOURCE:
                raise StaleProposalError("The workflow this request was computed from is no longer a declared one.")
            if declaration_digest(live.evidence) != declaration_digest(_provenance(approval)):
                raise StaleProposalError("The declaration this request was computed from has changed since it was requested.")
            return live

        return super().approve(recommendation_id, expected_proposal_version, note, decided_by=who,
                               evaluate=current if evaluate is not None else None)
