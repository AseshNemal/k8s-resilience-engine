import os
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.engine.recommendation_evaluation_validation.approval import (
    ApprovalModel,
    ApprovalService,
    ApprovalRepository,
    LiveRecommendation,
    StateTransitionError,
    VersionConflictError,
    PreconditionError,
    ResearchApprovalService,
    has_research_provenance,
    has_verified_provenance,
)
from app.engine.recommendation_evaluation_validation.application import (
    PRODUCTION,
    RESEARCH,
    AppliedChangeRepository,
    ApplicationRefused,
    ApplicationService,
    ApplyGateway,
    ChangeAnalysisRepository,
)
from app.engine.recommendation_evaluation_validation.validation.experiment_repository import ExperimentRepository
from app.engine.recommendation_evaluation_validation.application.analysis import ChangeAnalyser
from app.engine.recommendation_evaluation_validation.application.live_analyser import ProductionAnalyser, ResearchAnalyser
from app.engine.recommendation_evaluation_validation.approval.research import declaration_digest
from app.engine.recommendation_evaluation_validation.evidence_context import build_recommendation_context
from app.engine.recommendation_evaluation_validation.explanation import describe_live_recommendation
from app.engine.recommendation_evaluation_validation.approval.research import RESEARCH_SCOPE
from app.engine.recommendation_evaluation_validation.declared_workflow import NOT_OBSERVED
from app.engine.recommendation_evaluation_validation.live_pipeline import (
    build_declared_failures,
    build_declared_recommendation,
    build_live_failures,
    build_live_recommendation,
)
from app.engine.recommendation_evaluation_validation.portfolio_pipeline import (
    build_declared_portfolio,
    build_frozen_portfolio,
    describe_portfolio,
)
from app.engine.recommendation_evaluation_validation.yaml_proposal import GENERATED, build_yaml_proposal
from app.shared.evidence_errors import EvidenceError, unavailable
from app.integrations.kubernetes.client import NotConnectedError
from app.integrations.kubernetes.validation_gateway import AmbientCredentialsError, KubernetesTestClusterGateway
from app.core.config import get_settings
from app.core.database import get_engine
from sqlalchemy.orm import Session

from app.shared.schemas.recommendation import RecommendationReport
from app.shared.schemas.approval import (
    ApprovalRequest,
    ApprovalDecisionRequest,
    ApprovalResponse,
)
from app.shared.schemas.base import APIModel
from app.api.validation_loop import require_no_validation_run, router as validation_loop_router


class ResearchApprovalRequest(APIModel):
    """A request to approve a DECLARED-workflow recommendation for research validation. Member 4's own body; no shared schema changes."""

    recommendation_id: str
    proposal_version: str
    acknowledged_by: str
    acknowledgement: str


class ResearchDecisionRequest(APIModel):
    note: str
    expected_proposal_version: str
    decided_by: str

class ApplyRequest(APIModel):
    """Who is applying an approved change to the connected cluster. Member 4's own body; no shared schema changes."""

    applied_by: str


class ResetRequest(APIModel):
    reset_by: str


router = APIRouter(
    prefix="/api/recommendations",
    tags=["Recommendation Evaluation"],
)


def get_db():
    engine = get_engine(get_settings().database_url)
    with Session(engine) as session:
        yield session

def get_approval_service(db: Session = Depends(get_db)) -> ApprovalService:
    repository = ApprovalRepository(db)
    return ApprovalService(repository)


def get_research_approval_service(db: Session = Depends(get_db)) -> ResearchApprovalService:
    """The research tier's gate, used only by the /declared routes. The production routes keep ``get_approval_service``."""
    return ResearchApprovalService(ApprovalRepository(db))


def get_recommendation_provider(
    workflow_id: str | None = Query(None, description="Frozen workflow to recommend for (required)"),
    failed_node: str | None = Query(None, description="Worker to protect against; default: the one whose loss "
                                                       "would hurt most"),
    namespace: str = Query("default", description="Kubernetes namespace collected by Component 2"),
) -> Callable[[], LiveRecommendation]:
    """Supplies the function that produces the recommendation (and live evidence) an approval is bound to.

    The recommendation is computed from the connected cluster and the named frozen workflow
    (live_pipeline.py); it never falls back to sample data. Without a workflow there is nothing to compute
    for, which is said explicitly. The provider is called inside the handler, after the request body has
    been validated, so a malformed request is still a 422. Tests substitute it with
    ``app.dependency_overrides``.
    """

    def provide() -> LiveRecommendation:
        if not workflow_id:
            raise unavailable(
                "RECOMMENDATION_EVIDENCE_UNAVAILABLE",
                "Name the frozen workflow to recommend for (workflow_id). No recommendation has been generated "
                "from live cluster evidence, so there is nothing to approve.",
                "recommendation",
            )
        try:
            return build_live_recommendation(workflow_id, failed_node, namespace)
        except ValueError as error:  # a blank identifier
            raise EvidenceError(
                "INVALID_RECOMMENDATION_REQUEST", str(error), http_status=422, component="recommendation"
            ) from error

    return provide


DECLARED_NOT_APPROVABLE = (
    "This recommendation was computed from a workflow a person declared, not one Component 1 observed from traces, approved and "
    "froze. It can be read and studied. The production approval cannot be used for it; a named person can approve it for research "
    "validation through the research tier, and anything measured from it is conditional on the declaration."
)
DECLARED_OUTCOME = "Evaluated from a declared workflow, not an observed one. Read the notice below before relying on it."


def get_declared_provider(
    workflow_id: str | None = Query(None, description="Declared workflow to evaluate (required)"),
    failed_node: str | None = Query(None, description="Worker to protect against; default: the one whose loss would hurt most"),
    namespace: str = Query("default", description="Kubernetes namespace collected by Component 2"),
) -> Callable[[], LiveRecommendation]:
    """Supplies the evaluation of a DECLARED workflow on the live topology. It has no approval, apply or validation path."""

    def provide() -> LiveRecommendation:
        if not workflow_id:
            raise unavailable("RECOMMENDATION_EVIDENCE_UNAVAILABLE", "Name the declared workflow to evaluate (workflow_id).",
                              "recommendation")
        try:
            return build_declared_recommendation(workflow_id, failed_node, namespace)
        except ValueError as error:
            raise EvidenceError("INVALID_RECOMMENDATION_REQUEST", str(error), http_status=422,
                                component="recommendation") from error

    return provide


def get_declared_evaluator() -> Callable[[ApprovalModel], LiveRecommendation]:
    """Re-evaluates, on the server, the DECLARED workflow and failure a research request was made for (never a frozen one).

    The workflow, namespace and failed node come from the provenance the server stored with the request, never from the client.
    """

    def evaluate(approval: ApprovalModel) -> LiveRecommendation:
        provenance = (approval.evaluation_snapshot or {}).get("provenance") or {}
        workflow_id, namespace = provenance.get("workflowId"), provenance.get("namespace")
        if not workflow_id or not namespace:
            raise PreconditionError("This approval request does not record the workflow and namespace it was computed for, so it "
                                    "cannot be re-evaluated; reject it and request a new approval.")
        try:
            return build_declared_recommendation(workflow_id, provenance.get("failedNode"), namespace)
        except ValueError as error:
            raise EvidenceError("INVALID_RECOMMENDATION_REQUEST", str(error), http_status=422, component="recommendation") from error

    return evaluate


def get_current_evaluator() -> Callable[[ApprovalModel], LiveRecommendation]:
    """Supplies the function that re-evaluates, on the server, the workflow and failure a request was made for.

    An approval is decided against the evaluation as it is now, not as it was when the request was written. The
    workflow, namespace and failed node come from the provenance the server stored with the request, never from
    the client. Tests substitute it with ``app.dependency_overrides``.
    """

    def evaluate(approval: ApprovalModel) -> LiveRecommendation:
        provenance = (approval.evaluation_snapshot or {}).get("provenance") or {}
        workflow_id, namespace = provenance.get("workflowId"), provenance.get("namespace")
        if not workflow_id or not namespace:
            raise PreconditionError(
                "This approval request does not record the workflow and namespace it was computed for, so it "
                "cannot be re-evaluated; reject it and request a new approval from a current recommendation.")
        try:
            return build_live_recommendation(workflow_id, provenance.get("failedNode"), namespace)
        except ValueError as error:
            raise EvidenceError(
                "INVALID_RECOMMENDATION_REQUEST", str(error), http_status=422, component="recommendation"
            ) from error

    return evaluate


def get_cluster_applier() -> Callable[[], ApplyGateway]:
    """Builds the gateway for the cluster the extension is CONNECTED to, able to write (Apply and Reset only).

    Developer-approved 2026-10-23: the connected cluster is the docker-desktop research cluster and the extension holds a
    full-access credential for it. This is the only place a gateway is built with ``connected_cluster=True`` and
    ``allow_writes=True``; it is reached only through the apply and reset routes below. Tests substitute it.
    """

    def build() -> ApplyGateway:
        first = next((p for p in (os.environ.get("KUBECONFIG") or "").split(os.pathsep) if p), "~/.kube/config")
        try:
            return KubernetesTestClusterGateway(first, connected_cluster=True, allow_writes=True)
        except AmbientCredentialsError:
            raise NotConnectedError("no cluster is connected") from None

    return build


CLUSTER_WRITES_ENV = "RESILIENCE_ALLOW_CLUSTER_WRITES"


def cluster_writes_enabled() -> bool:
    """Whether this deployment armed Apply and Reset. Read from the backend's environment, never from a request."""
    return os.environ.get(CLUSTER_WRITES_ENV) == "1"


def get_change_analyser() -> ChangeAnalyser:
    """How the production tier recalculates failure impact after a change (frozen workflow, live topology, Member 3)."""
    return ProductionAnalyser()


def get_research_change_analyser() -> ChangeAnalyser:
    """The research tier's: the declared workflow, live topology, Member 3. Tests substitute both."""
    return ResearchAnalyser()


def get_application_service(db: Session = Depends(get_db),
                            gateway: Callable[[], ApplyGateway] = Depends(get_cluster_applier),
                            analyser: ChangeAnalyser = Depends(get_change_analyser)) -> ApplicationService:
    return ApplicationService(ApprovalRepository(db), AppliedChangeRepository(db), gateway, tier=PRODUCTION,
                              trusted=has_verified_provenance, writes_enabled=cluster_writes_enabled(),
                              analyses=ChangeAnalysisRepository(db), analyser=analyser, experiments=ExperimentRepository(db))


def get_research_application_service(db: Session = Depends(get_db),
                                     gateway: Callable[[], ApplyGateway] = Depends(get_cluster_applier),
                                     analyser: ChangeAnalyser = Depends(get_research_change_analyser)) -> ApplicationService:
    """Applies a RESEARCH-approved recommendation. Its YAML is never 'applicable' at the production gate, by design."""
    return ApplicationService(ApprovalRepository(db), AppliedChangeRepository(db), gateway, tier=RESEARCH,
                              trusted=has_research_provenance, require_applicable=False,
                              writes_enabled=cluster_writes_enabled(),
                              analyses=ChangeAnalysisRepository(db), analyser=analyser, experiments=ExperimentRepository(db))


def get_research_application_evaluator(
    evaluate: Callable[[ApprovalModel], LiveRecommendation] = Depends(get_declared_evaluator),
) -> Callable[[ApprovalModel], LiveRecommendation]:
    """The declared evaluation, plus: the declaration it was approved against must be the declaration that is in force now."""

    def checked(approval: ApprovalModel) -> LiveRecommendation:
        live = evaluate(approval)
        if declaration_digest(live.evidence) != declaration_digest((approval.evaluation_snapshot or {}).get("provenance")):
            raise ApplicationRefused("APPROVAL_STALE", "The declared workflow changed since this was approved, so it is not what "
                                     "was approved. Request approval again.")
        return live

    return checked


def _to_response(approval) -> ApprovalResponse:
    return ApprovalResponse(
        id=approval.id,
        recommendation_id=approval.recommendation_id,
        proposal_version=approval.proposal_version,
        status=approval.status,
        requested_at=approval.requested_at,
        requested_by=approval.requested_by,
        decided_at=approval.decided_at,
        decided_by=approval.decided_by,
        decision_note=approval.decision_note,
        recommendation_snapshot=approval.recommendation_snapshot,
        yaml_snapshot=approval.yaml_snapshot,
        evaluation_snapshot=approval.evaluation_snapshot,
    )


@router.get(
    "/live",
    response_model=RecommendationReport,
    response_model_by_alias=True,
    responses={409: {"description": "No recommendation has been generated from live evidence"}},
)
def read_live_recommendation(
    response: Response,
    provider: Callable[[], LiveRecommendation] = Depends(get_recommendation_provider),
) -> RecommendationReport:
    """The current recommendation report and the live evidence it was computed from, or an explicit error."""
    live = provider()
    response.headers["X-Evidence-Source"] = str(live.evidence.get("source", ""))
    response.headers["X-Topology-Captured-At"] = str(live.evidence.get("topologyCapturedAt", ""))
    return live.report


@router.get(
    "/declared",
    response_model=RecommendationReport,
    response_model_by_alias=True,
    responses={409: {"description": "Required evidence is not available"}},
)
def read_declared_recommendation(
    response: Response,
    provider: Callable[[], LiveRecommendation] = Depends(get_declared_provider),
) -> RecommendationReport:
    """The recommendation for a workflow a person DECLARED, on the connected cluster's live topology. Never approvable."""
    live = provider()
    response.headers["X-Evidence-Source"] = str(live.evidence.get("source", ""))
    response.headers["X-Evidence-Approvable"] = "false"
    response.headers["X-Topology-Captured-At"] = str(live.evidence.get("topologyCapturedAt", ""))
    return live.report


@router.get("/declared/detail")
def read_declared_recommendation_detail(
    response: Response,
    provider: Callable[[], LiveRecommendation] = Depends(get_declared_provider),
) -> dict:
    """The declared recommendation with what the page shows, with approval switched off for every candidate and the reason given.

    The engine's own eligibility verdict does not look at where the evidence came from (the approval gate does, later), so this route
    overrides it: whatever the ranking says, nothing here is ready for approval.
    """
    live = provider()
    response.headers["X-Evidence-Source"] = str(live.evidence.get("source", ""))
    response.headers["X-Evidence-Approvable"] = "false"
    response.headers["X-Topology-Captured-At"] = str(live.evidence.get("topologyCapturedAt", ""))
    shown = describe_live_recommendation(live)
    for candidate in shown["candidates"]:
        eligible = bool(candidate["approval"]["ready"])      # the engine's own verdict on feasibility and ranking, before the override
        candidate["approval"] = {"ready": False, "reason": DECLARED_NOT_APPROVABLE, "requiresJustification": False,
                                 "declared": True}
        # The research tier is a separate door with its own routes and a named acknowledgement; it never reopens the production controls above.
        candidate["researchApproval"] = {
            "available": eligible, "scope": RESEARCH_SCOPE if eligible else None,
            "requestWith": f"POST /api/recommendations/declared/{candidate['id']}/request-approval" if eligible else None,
            "requires": "the name of the person and a written acknowledgement that the workflow is declared, not observed",
            "reason": None if eligible else "This candidate is not verified feasible and ranked, so it cannot be approved in any tier."}
    if shown["outcome"]["state"] == "READY":
        shown["outcome"] = {"state": "DECLARED_NOT_APPROVABLE", "message": DECLARED_OUTCOME}
    shown["declared"] = {**(live.evidence.get("declaredWorkflow") or {}), "approvable": False, "limits": NOT_OBSERVED,
                         "notApprovableBecause": DECLARED_NOT_APPROVABLE}
    shown["schedulingFacts"] = live.evidence.get("schedulingFacts")
    return shown


@router.get(
    "/portfolio/detail",
    responses={409: {"description": "Required evidence is not available"}},
)
def read_portfolio_detail(
    response: Response,
    source: str = Query("frozen", pattern="^(frozen|declared)$",
                        description="Which workflows: every approved frozen one, or every declared one. They are never mixed."),
    failed_node: str | None = Query(None, description="Worker to protect against; default: the one whose loss would hurt most"),
    namespace: str = Query("default", description="Kubernetes namespace collected by Component 2"),
) -> dict:
    """The recommendation for ALL workflows at once, weighted by criticality. Read-only; approval is shut for every candidate.

    It has its own route and its own evidence source, so the approval, apply and validation routes (which take one named workflow and
    re-evaluate it themselves) can never be reached with it. A person picks a candidate here, then reviews it for one workflow.
    """
    build = build_declared_portfolio if source == "declared" else build_frozen_portfolio
    try:
        live = build(namespace, failed_node)
    except ValueError as error:
        raise EvidenceError("INVALID_RECOMMENDATION_REQUEST", str(error), http_status=422, component="recommendation") from error
    response.headers["X-Evidence-Source"] = str(live.evidence.get("source", ""))
    response.headers["X-Evidence-Approvable"] = "false"
    response.headers["X-Topology-Captured-At"] = str(live.evidence.get("topologyCapturedAt", ""))
    shown = describe_portfolio(live)
    shown["schedulingFacts"] = live.evidence.get("schedulingFacts")
    return shown


@router.get(
    "/declared/{recommendation_id}/approval",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def get_research_approval(
    recommendation_id: str,
    response: Response,
    service: ResearchApprovalService = Depends(get_research_approval_service),
) -> ApprovalResponse:
    approval = service.get_approval(recommendation_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    response.headers["X-Evidence-Provenance"] = "research-declared" if has_research_provenance(approval) else "unverified"
    return _to_response(approval)


@router.post(
    "/declared/{recommendation_id}/request-approval",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def request_research_approval(
    recommendation_id: str,
    request: ResearchApprovalRequest,
    response: Response,
    service: ResearchApprovalService = Depends(get_research_approval_service),
    provider: Callable[[], LiveRecommendation] = Depends(get_declared_provider),
) -> ApprovalResponse:
    """Put a DECLARED-workflow recommendation to the research tier. Needs a named person and a written acknowledgement.

    The production approval routes are untouched and still refuse what this makes. Nothing is applied.
    """
    live = provider()
    proposal = build_yaml_proposal(live, recommendation_id)
    try:
        approval = service.request_research_approval(
            recommendation_id, request.proposal_version, live, acknowledged_by=request.acknowledged_by,
            acknowledgement=request.acknowledgement, proposal_yaml=proposal.yaml if proposal.status == GENERATED else None)
    except PreconditionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    response.headers["X-Evidence-Provenance"] = "research-declared"
    return _to_response(approval)


@router.post(
    "/declared/{recommendation_id}/approve",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def approve_research_recommendation(
    recommendation_id: str,
    request: ResearchDecisionRequest,
    service: ResearchApprovalService = Depends(get_research_approval_service),
    evaluator: Callable[[ApprovalModel], LiveRecommendation] = Depends(get_declared_evaluator),
) -> ApprovalResponse:
    try:
        approval = service.approve(
            recommendation_id=recommendation_id, expected_proposal_version=request.expected_proposal_version,
            note=request.note, decided_by=request.decided_by, evaluate=evaluator)
    except PreconditionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except (StateTransitionError, VersionConflictError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_response(approval)


@router.post(
    "/declared/{recommendation_id}/reject",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def reject_research_recommendation(
    recommendation_id: str,
    request: ResearchDecisionRequest,
    service: ResearchApprovalService = Depends(get_research_approval_service),
) -> ApprovalResponse:
    try:
        approval = service.reject(
            recommendation_id=recommendation_id, expected_proposal_version=request.expected_proposal_version,
            note=request.note, decided_by=request.decided_by)
    except PreconditionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except (StateTransitionError, VersionConflictError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_response(approval)


@router.get("/live/failures")
def read_live_failures(
    response: Response,
    workflow_id: str | None = Query(None, description="Frozen workflow to assess (required)"),
    namespace: str = Query("default", description="Kubernetes namespace collected by Component 2"),
) -> dict:
    """Every worker node assessed as the failure, each with its own selected candidate (read-only; nothing is applied)."""
    if not workflow_id:
        raise unavailable("RECOMMENDATION_EVIDENCE_UNAVAILABLE", "Name the frozen workflow to assess (workflow_id).", "recommendation")
    try:
        overview = build_live_failures(workflow_id, namespace)
    except ValueError as error:
        raise EvidenceError("INVALID_RECOMMENDATION_REQUEST", str(error), http_status=422, component="recommendation") from error
    response.headers["X-Evidence-Source"] = overview["source"]
    return overview


@router.get("/declared/failures")
def read_declared_failures(
    response: Response,
    workflow_id: str | None = Query(None, description="Declared workflow to assess (required)"),
    namespace: str = Query("default", description="Kubernetes namespace collected by Component 2"),
) -> dict:
    """The same overview for a DECLARED workflow. Labelled declared-workflow; nothing here is approvable."""
    if not workflow_id:
        raise unavailable("RECOMMENDATION_EVIDENCE_UNAVAILABLE", "Name the declared workflow to assess (workflow_id).", "recommendation")
    try:
        overview = build_declared_failures(workflow_id, namespace)
    except ValueError as error:
        raise EvidenceError("INVALID_RECOMMENDATION_REQUEST", str(error), http_status=422, component="recommendation") from error
    response.headers["X-Evidence-Source"] = overview["source"]
    response.headers["X-Evidence-Approvable"] = "false"
    return overview


@router.get("/context")
def read_recommendation_context(
    namespace: str | None = Query(None, description="Application namespace; default: the only one with applications"),
    workflow_id: str | None = Query(None, description="Frozen workflow; default: the only one that applies"),
) -> dict:
    """Which application and frozen workflow the page can use, and which evidence is missing, and what to do.

    Read-only and always answers: a missing piece of evidence is the answer, not an error. It lists the approved
    frozen workflows and whether each applies to the application's services; it builds none of them.
    """
    return build_recommendation_context(namespace, workflow_id)


@router.get(
    "/live/detail",
    responses={409: {"description": "No recommendation has been generated from live evidence"}},
)
def read_live_recommendation_detail(
    response: Response,
    provider: Callable[[], LiveRecommendation] = Depends(get_recommendation_provider),
) -> dict:
    """The live recommendation with what the page shows for each candidate: root cause, the change, feasibility and
    why, current placement, predicted before and after, cost, ranking and whether it may be put to approval.

    Same inputs and same coded errors as ``/live``. Everything in it was computed by the engine; nothing is applied.
    """
    live = provider()
    response.headers["X-Evidence-Source"] = str(live.evidence.get("source", ""))
    response.headers["X-Topology-Captured-At"] = str(live.evidence.get("topologyCapturedAt", ""))
    return describe_live_recommendation(live)


@router.get(
    "/{recommendation_id}/approval",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def get_approval(
    recommendation_id: str,
    response: Response,
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalResponse:
    approval = service.get_approval(recommendation_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    response.headers["X-Evidence-Provenance"] = "verified" if has_verified_provenance(approval) else "unverified"
    return _to_response(approval)


@router.post(
    "/{recommendation_id}/request-approval",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def request_approval(
    recommendation_id: str,
    request: ApprovalRequest,
    response: Response,
    service: ApprovalService = Depends(get_approval_service),
    provider: Callable[[], LiveRecommendation] = Depends(get_recommendation_provider),
) -> ApprovalResponse:
    live = provider()
    proposal = build_yaml_proposal(live, recommendation_id)
    try:
        approval = service.request_approval(
            recommendation_id=recommendation_id,
            proposal_version=request.proposal_version,
            recommendation=live.report,
            yaml_snapshot=request.yaml_snapshot,
            requested_by="developer",
            evidence=live.evidence,
            proposal_yaml=proposal.yaml if proposal.status == GENERATED and proposal.applicable else None,
        )
    except PreconditionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    response.headers["X-Evidence-Provenance"] = "verified" if has_verified_provenance(approval) else "unverified"
    return _to_response(approval)


@router.post(
    "/{recommendation_id}/approve",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def approve_recommendation(
    recommendation_id: str,
    request: ApprovalDecisionRequest,
    service: ApprovalService = Depends(get_approval_service),
    evaluator: Callable[[ApprovalModel], LiveRecommendation] = Depends(get_current_evaluator),
) -> ApprovalResponse:
    try:
        approval = service.approve(
            recommendation_id=recommendation_id,
            expected_proposal_version=request.expected_proposal_version,
            note=request.note,
            decided_by="developer",
            evaluate=evaluator,
        )
    except PreconditionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except VersionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return _to_response(approval)


@router.post(
    "/{recommendation_id}/reject",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def reject_recommendation(
    recommendation_id: str,
    request: ApprovalDecisionRequest,
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalResponse:
    try:
        approval = service.reject(
            recommendation_id=recommendation_id,
            expected_proposal_version=request.expected_proposal_version,
            note=request.note,
            decided_by="developer",
        )
    except PreconditionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except VersionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return _to_response(approval)


# --- Apply and Reset ----------------------------------------------------------------------------------------------------------------
# An approval applies nothing. These routes are the separate, named step that applies an approved change to the connected cluster and
# puts it back (developer-approved 2026-10-23). The checks are in ``ApplicationService``; the routes only choose the tier.


def _applied(service: ApplicationService, recommendation_id: str, action: Callable[[], object]) -> dict:
    try:
        action()
    except PreconditionError as error:
        raise HTTPException(status_code=422, detail=str(error))
    return service.status(recommendation_id)


@router.get("/applications/current")
def get_current_application(
    production: ApplicationService = Depends(get_application_service),
    research: ApplicationService = Depends(get_research_application_service),
) -> dict:
    """The most recent applied change of either tier, with its live status, whichever recommendation is selected on the page.

    This is how a reloaded page finds the operation again without repeating Apply: the change is stored server-side under its own
    application id, independent of the current rankings. It reads and records progress; it applies nothing.
    """
    last = production.repository.latest()
    if last is None:
        return {"application": None, "tier": None}
    service = research if last.tier == RESEARCH else production
    return service.status(last.recommendation_id)


@router.get("/applications")
def list_applications(limit: int = Query(20, ge=1, le=100), production: ApplicationService = Depends(get_application_service)) -> dict:
    """Recent applied changes, newest first, as stored (no cluster read): the operation history."""
    return {"applications": [{
        "applicationId": c.application_id, "recommendationId": c.recommendation_id, "tier": c.tier, "namespace": c.namespace,
        "status": c.status, "appliedBy": c.applied_by, "appliedAt": c.applied_at.isoformat(),
        "resetAt": c.reset_at.isoformat() if c.reset_at else None, "deployments": c.deployments,
        "rolloutState": (c.rollout or {}).get("state")} for c in production.repository.recent(limit)]}


@router.get("/{recommendation_id}/application")
def get_application(recommendation_id: str, service: ApplicationService = Depends(get_application_service)) -> dict:
    return service.status(recommendation_id)


@router.post("/{recommendation_id}/apply")
def apply_recommendation(
    recommendation_id: str,
    request: ApplyRequest,
    _no_validation_run: None = Depends(require_no_validation_run),
    service: ApplicationService = Depends(get_application_service),
    evaluator: Callable[[ApprovalModel], LiveRecommendation] = Depends(get_current_evaluator),
) -> dict:
    return _applied(service, recommendation_id, lambda: service.apply(recommendation_id, request.applied_by, evaluate=evaluator))


@router.post("/{recommendation_id}/reset")
def reset_recommendation(
    recommendation_id: str,
    request: ResetRequest,
    _no_validation_run: None = Depends(require_no_validation_run),
    service: ApplicationService = Depends(get_application_service),
) -> dict:
    return _applied(service, recommendation_id, lambda: service.reset(recommendation_id, request.reset_by))


@router.post("/{recommendation_id}/analysis/retry")
def retry_analysis(recommendation_id: str, service: ApplicationService = Depends(get_application_service)) -> dict:
    """Run a failed post-change recalculation again. It never applies or resets anything."""
    return _applied(service, recommendation_id, lambda: service.retry_analysis(recommendation_id))


@router.post("/declared/{recommendation_id}/analysis/retry")
def retry_research_analysis(recommendation_id: str,
                            service: ApplicationService = Depends(get_research_application_service)) -> dict:
    return _applied(service, recommendation_id, lambda: service.retry_analysis(recommendation_id))


@router.get("/declared/{recommendation_id}/application")
def get_research_application(recommendation_id: str,
                             service: ApplicationService = Depends(get_research_application_service)) -> dict:
    return service.status(recommendation_id)


@router.post("/declared/{recommendation_id}/apply")
def apply_research_recommendation(
    recommendation_id: str,
    request: ApplyRequest,
    _no_validation_run: None = Depends(require_no_validation_run),
    service: ApplicationService = Depends(get_research_application_service),
    evaluator: Callable[[ApprovalModel], LiveRecommendation] = Depends(get_research_application_evaluator),
) -> dict:
    return _applied(service, recommendation_id, lambda: service.apply(recommendation_id, request.applied_by, evaluate=evaluator))


@router.post("/declared/{recommendation_id}/reset")
def reset_research_recommendation(
    recommendation_id: str,
    request: ResetRequest,
    _no_validation_run: None = Depends(require_no_validation_run),
    service: ApplicationService = Depends(get_research_application_service),
) -> dict:
    return _applied(service, recommendation_id, lambda: service.reset(recommendation_id, request.reset_by))


# The fault-injection validation loop ("Prove it"). Included last so that none of the `/{recommendation_id}/...` routes above can shadow it.
router.include_router(validation_loop_router, prefix="/validation-loop")
