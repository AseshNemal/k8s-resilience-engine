"""The approval gate: only a verified-feasible, ranked candidate with a positive score can be put to a developer.

Everything the gate decides comes from the server's own evaluation (the recommendation provider at request
time, a fresh re-evaluation at approval time). A rank, score or selection sent by a client is never read.
The top-ranked candidate stays the automatic selection; another ranked candidate needs a written
justification, carried in the existing approval note so no shared schema changes.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.recommendations import get_current_evaluator, get_recommendation_provider
from app.core.database import get_engine
from app.engine.recommendation_evaluation_validation.approval import (
    LIVE_EVIDENCE_SOURCE,
    proposal_fingerprint,
    ApprovalModel,
    ApprovalRepository,
    ApprovalService,
    LiveRecommendation,
    PreconditionError,
    evaluate_eligibility,
)
from app.main import app
from app.shared.schemas.approval import ApprovalStatus
from app.shared.schemas.recommendation import RecommendationCandidate, RecommendationReport
from tests.recommendation_evaluation_validation.test_approval_api import (  # noqa: F401  (fixtures)
    client,
    override_get_db,
    sqlite_url,
)

JUSTIFICATION = "Top candidate restarts the payment service during the sale; this one touches only the cache."


@dataclass
class Spec:
    id: str
    rank: int | None = None
    score: float | None = 0.0
    feasible: bool = True
    excluded: str | None = None
    plan: str = "a"                 # which placement the evaluation proposes; a different value is a different plan
    constraints: str = "a"          # the digest of the scheduling constraints the verdict was reached under
    blast_after: float = 0.0
    members: tuple[str, ...] = ()   # for a combination


def plan_of(spec: Spec) -> dict:
    return {f"{spec.id}-0": f"node-{spec.plan}-1", f"{spec.id}-1": f"node-{spec.plan}-2"}


class Source:
    """A controllable server-side evaluation: it is both the provider and the re-evaluator."""

    def __init__(self) -> None:
        self.specs: list[Spec] = []
        self.selected: str | None = None
        self.with_ranking = True
        self.with_plan = True
        self.provenance_extra: dict = {}
        self.evaluations = 0

    def set(self, *specs: Spec, selected: str | None = ...):  # type: ignore[assignment]
        self.specs = list(specs)
        self.selected = next((s.id for s in specs if s.rank == 1), None) if selected is ... else selected
        return self

    def __call__(self) -> LiveRecommendation:
        self.evaluations += 1
        report = RecommendationReport(
            workflow_id="wf1",
            recommendations=[RecommendationCandidate(
                id=s.id, type="TOPOLOGY_SPREAD", service=s.id, feasible=s.feasible, predicted_blast_radius=0.0,
                risk_reduction=0.0, resource_cost="LOW", confidence=1.0, score=s.score or 0.0) for s in self.specs],
            selected_recommendation_id=self.selected)
        evidence = {"source": LIVE_EVIDENCE_SOURCE, "topologyCapturedAt": "2026-01-01T00:00:00Z",
                    "workflowId": "wf1", "namespace": "shop", "failedNode": "w1"} | self.provenance_extra
        if self.with_ranking:
            evidence["ranking"] = {"formulaVersion": "1", "candidates": {
                s.id: {"id": s.id, "rank": s.rank, "score": s.score if s.rank else None, "excluded": s.excluded,
                       "components": {"blastBefore": 100.0, "blastAfter": s.blast_after}}
                for s in self.specs}}
        if self.with_plan:
            evidence["feasibility"] = {s.id: {"status": "FEASIBLE" if s.feasible else "INSUFFICIENT_EVIDENCE",
                                              "placement": plan_of(s) if s.feasible else {},
                                              "podsToRecreate": [f"{s.id}-1"] if s.feasible else [],
                                              "constraintsDigest": f"digest-{s.constraints}"}
                                       for s in self.specs}
            evidence["combinations"] = {"entries": {s.id: {"members": list(s.members)} for s in self.specs if s.members}}
        return LiveRecommendation(report=report, evidence=evidence)


TOP, SECOND = Spec("top", rank=1, score=0.9), Spec("second", rank=2, score=0.5)
IDLE = Spec("idle", score=0.0, excluded="NO_PREDICTED_IMPROVEMENT")                    # feasible, not ranked
TRANSFER = Spec("transfer", score=0.0, excluded="RISK_TRANSFER_ONLY")
NOT_VERIFIED = Spec("unverified", feasible=False, score=0.0, excluded="INSUFFICIENT_EVIDENCE")


@pytest.fixture
def source(override_get_db):
    src = Source().set(TOP, SECOND, IDLE, TRANSFER, NOT_VERIFIED)
    app.dependency_overrides[get_recommendation_provider] = lambda: src
    app.dependency_overrides[get_current_evaluator] = lambda: (lambda approval: src())
    yield src
    app.dependency_overrides.pop(get_recommendation_provider, None)
    app.dependency_overrides.pop(get_current_evaluator, None)


@pytest.fixture
def session(sqlite_url):
    with Session(get_engine(sqlite_url)) as db:
        yield db


def request(client: TestClient, rec_id: str, **extra):
    return client.post(f"/api/recommendations/{rec_id}/request-approval",
                       json={"recommendationId": rec_id, "proposalVersion": "v1", **extra})


def decide(client: TestClient, rec_id: str, action: str = "approve", note: str = "", version: str = "v1"):
    return client.post(f"/api/recommendations/{rec_id}/{action}", json={"expectedProposalVersion": version, "note": note})


def status_of(client: TestClient, rec_id: str) -> str:
    return client.get(f"/api/recommendations/{rec_id}/approval").json()["status"]


# --- who may be put forward ------------------------------------------------------------------------

def test_the_top_ranked_candidate_can_be_requested_and_is_marked_as_the_selection(client, source):
    response = request(client, "top")

    assert response.status_code == 200
    selection = response.json()["evaluationSnapshot"]["selection"]
    assert selection["isSelected"] is True and selection["rank"] == 1
    assert selection["selectedRecommendationId"] == "top" and selection["requiresJustification"] is False


@pytest.mark.parametrize("rec_id", ["idle", "transfer"])
def test_a_feasible_candidate_that_is_not_ranked_is_refused_even_though_feasible_is_true(client, source, rec_id):
    candidate = next(c for c in source().report.recommendations if c.id == rec_id)
    assert candidate.feasible is True        # the loophole this gate closes

    response = request(client, rec_id)

    assert response.status_code == 422
    assert "not ranked" in response.json()["detail"]
    assert client.get(f"/api/recommendations/{rec_id}/approval").status_code == 404     # nothing was recorded


def test_a_candidate_that_is_not_verified_feasible_is_refused(client, source):
    assert request(client, "unverified").status_code == 422


def test_a_candidate_missing_from_the_evaluation_is_refused(client, source):
    assert request(client, "nope").status_code == 422


def test_without_authoritative_ranking_evidence_nothing_is_eligible(client, source):
    source.with_ranking = False

    response = request(client, "top")

    assert response.status_code == 422 and "ranking" in response.json()["detail"]


def test_a_positive_score_in_the_report_is_not_enough_when_the_ranking_says_excluded(client, source):
    source.set(Spec("sneaky", rank=None, score=0.7, excluded="RISK_TRANSFER_ONLY"), selected=None)

    assert request(client, "sneaky").status_code == 422


def test_a_zero_score_is_not_eligible_even_when_ranked(client, source):
    source.set(Spec("zero", rank=1, score=0.0), selected="zero")

    assert request(client, "zero").status_code == 422


def test_an_evaluation_that_disagrees_with_itself_about_the_selection_is_refused(client, source):
    source.set(TOP, SECOND, selected="second")            # the report selects rank 2; the ranking says rank 1 is top

    assert request(client, "top").status_code == 422
    assert request(client, "second").status_code == 422


def test_rank_score_and_selection_sent_by_the_client_are_ignored(client, source):
    forged = {"rank": 1, "score": 9.0, "isSelected": True, "selected": True, "excluded": None,
              "justification": JUSTIFICATION, "selection": {"rank": 1, "isSelected": True}}

    refused = request(client, "idle", **forged)
    second = request(client, "second", **forged)

    assert refused.status_code == 422
    assert second.status_code == 200
    selection = second.json()["evaluationSnapshot"]["selection"]
    assert selection["rank"] == 2 and selection["isSelected"] is False and selection["requiresJustification"] is True
    assert selection["score"] == pytest.approx(0.5)


# --- approving ---------------------------------------------------------------------------------------

def test_the_selected_candidate_is_approved_as_before_with_no_special_note(client, source):
    request(client, "top")

    response = decide(client, "top", note="")

    assert response.status_code == 200 and response.json()["status"] == "APPROVED"


@pytest.mark.parametrize("note", ["", "   ", "ok", "x" * 19, " " * 25, "\n\t " * 10])
def test_another_ranked_candidate_needs_a_documented_justification(client, source, note):
    request(client, "second")

    response = decide(client, "second", note=note)

    assert response.status_code == 422
    assert "justification" in response.json()["detail"] and "top" in response.json()["detail"]
    assert status_of(client, "second") == "PENDING_APPROVAL"


def test_another_ranked_candidate_can_be_approved_with_a_justification_which_is_recorded(client, source):
    request(client, "second")

    response = decide(client, "second", note=JUSTIFICATION)

    assert response.status_code == 200 and response.json()["status"] == "APPROVED"
    assert response.json()["decisionNote"] == JUSTIFICATION
    check = response.json()["evaluationSnapshot"]["approvalCheck"]
    assert (check["rank"], check["selectedRecommendationId"], check["isSelected"]) == (2, "top", False)
    assert check["justification"] == JUSTIFICATION and check["checkedAt"]


def test_the_justification_length_is_configurable():
    from unittest.mock import MagicMock

    pending = MagicMock()
    pending.status = ApprovalStatus.PENDING_APPROVAL
    pending.proposal_version = "v1"
    current = Source().set(TOP, SECOND)()
    pending.evaluation_snapshot = {"provenance": {"source": LIVE_EVIDENCE_SOURCE},
                                   "proposal": {"fingerprint": proposal_fingerprint(current, "second"), "yamlSha256": None}}
    pending.yaml_snapshot = None
    repository = MagicMock(spec=ApprovalRepository)
    repository.get_by_recommendation_id.return_value = pending
    evaluate = lambda approval: current      # noqa: E731

    with pytest.raises(PreconditionError):
        ApprovalService(repository, min_justification_chars=30).approve("second", "v1", "short reason", evaluate=evaluate)
    ApprovalService(repository, min_justification_chars=5).approve("second", "v1", "short reason", evaluate=evaluate)


def test_a_pending_request_is_re_checked_against_a_fresh_evaluation_and_cannot_bypass_the_policy(client, source):
    request(client, "second")
    source.set(TOP, Spec("second", score=0.0, excluded="NO_PREDICTED_IMPROVEMENT"))     # the cluster has moved on

    response = decide(client, "second", note=JUSTIFICATION)

    assert response.status_code == 422 and "not ranked" in response.json()["detail"]
    assert status_of(client, "second") == "PENDING_APPROVAL"


def test_a_candidate_that_became_the_top_no_longer_needs_a_justification(client, source):
    request(client, "second")
    source.set(Spec("second", rank=1, score=0.9), Spec("top", rank=2, score=0.5))

    assert decide(client, "second", note="").status_code == 200


def test_a_candidate_that_stopped_being_the_top_now_needs_one(client, source):
    request(client, "top")
    source.set(Spec("top", rank=2, score=0.5), Spec("second", rank=1, score=0.9))

    assert decide(client, "top", note="").status_code == 422
    assert decide(client, "top", note=JUSTIFICATION).status_code == 200


def test_a_record_written_before_the_gate_is_judged_by_the_current_evaluation_not_its_stored_score(client, source, session):
    # Verified provenance, an old-style score, no selection data: what a pre-gate pending request looks like.
    ApprovalRepository(session).save(ApprovalModel(
        id=0, recommendation_id="idle", proposal_version="v1", status=ApprovalStatus.PENDING_APPROVAL,
        requested_at=datetime.now(timezone.utc), requested_by="developer",
        recommendation_snapshot={"id": "idle"},
        evaluation_snapshot={"score": 0.95, "provenance": {
            "source": LIVE_EVIDENCE_SOURCE, "workflowId": "wf1", "namespace": "shop", "failedNode": "w1"}}))

    response = decide(client, "idle", note=JUSTIFICATION)

    assert response.status_code == 422 and "not ranked" in response.json()["detail"]
    assert status_of(client, "idle") == "PENDING_APPROVAL"


def test_a_record_written_before_the_gate_is_refused_even_when_its_candidate_is_currently_eligible(client, source, session):
    # Eligibility now is not enough: nothing says this request was made for the proposal that is current, so it is
    # refused as predating the binding and has to be requested again.
    ApprovalRepository(session).save(ApprovalModel(
        id=0, recommendation_id="top", proposal_version="v1", status=ApprovalStatus.PENDING_APPROVAL,
        requested_at=datetime.now(timezone.utc), requested_by="developer", recommendation_snapshot={"id": "top"},
        evaluation_snapshot={"provenance": {
            "source": LIVE_EVIDENCE_SOURCE, "workflowId": "wf1", "namespace": "shop", "failedNode": "w1"}}))

    response = decide(client, "top")

    assert response.status_code == 409 and response.json()["detail"]["code"] == "APPROVAL_PROPOSAL_STALE"
    assert status_of(client, "top") == "PENDING_APPROVAL"


def test_a_verified_record_that_does_not_say_what_to_re_evaluate_cannot_be_approved(client, source, session):
    ApprovalRepository(session).save(ApprovalModel(
        id=0, recommendation_id="top", proposal_version="v1", status=ApprovalStatus.PENDING_APPROVAL,
        requested_at=datetime.now(timezone.utc), requested_by="developer", recommendation_snapshot={"id": "top"},
        evaluation_snapshot={"provenance": {"source": LIVE_EVIDENCE_SOURCE}}))
    app.dependency_overrides.pop(get_current_evaluator, None)      # the real evaluator reads the provenance

    response = decide(client, "top")

    assert response.status_code == 422 and "re-evaluated" in response.json()["detail"]
    assert status_of(client, "top") == "PENDING_APPROVAL"


def test_a_pending_request_can_always_be_rejected_because_rejecting_applies_nothing(client, source):
    request(client, "second")
    source.set(TOP, Spec("second", score=0.0, excluded="NO_PREDICTED_IMPROVEMENT"))

    assert decide(client, "second", action="reject", note="not worth it").status_code == 200


# --- the existing checks are untouched -----------------------------------------------------------------

def test_the_version_check_still_comes_first_and_does_not_need_an_evaluation(client, source):
    request(client, "top")
    evaluations_before = source.evaluations

    response = decide(client, "top", version="v-other")

    assert response.status_code == 409
    assert source.evaluations == evaluations_before


def test_an_unverified_record_is_still_refused_by_provenance_before_any_evaluation(client, source, session):
    ApprovalRepository(session).save(ApprovalModel(
        id=0, recommendation_id="top", proposal_version="v1", status=ApprovalStatus.PENDING_APPROVAL,
        requested_at=datetime.now(timezone.utc), requested_by="developer", recommendation_snapshot={},
        evaluation_snapshot={"score": 0.9}))
    evaluations_before = source.evaluations

    response = decide(client, "top")

    assert response.status_code == 409 and response.json()["detail"]["code"] == "APPROVAL_PROVENANCE_UNVERIFIED"
    assert source.evaluations == evaluations_before


def test_provenance_and_the_ranking_evidence_are_stored_with_the_request(client, source):
    request(client, "top")

    snapshot = client.get("/api/recommendations/top/approval").json()["evaluationSnapshot"]

    assert snapshot["provenance"]["source"] == LIVE_EVIDENCE_SOURCE
    assert snapshot["provenance"]["ranking"]["candidates"]["top"]["rank"] == 1


def test_an_already_decided_request_is_still_a_state_conflict(client, source):
    request(client, "top")
    decide(client, "top")

    assert decide(client, "top").status_code == 409


def test_without_a_current_evaluation_the_service_fails_closed():
    from unittest.mock import MagicMock

    pending = MagicMock()
    pending.status = ApprovalStatus.PENDING_APPROVAL
    pending.proposal_version = "v1"
    pending.evaluation_snapshot = {"provenance": {"source": LIVE_EVIDENCE_SOURCE}}
    repository = MagicMock(spec=ApprovalRepository)
    repository.get_by_recommendation_id.return_value = pending

    with pytest.raises(PreconditionError, match="current evaluation"):
        ApprovalService(repository).approve("top", "v1", "go")

    repository.update.assert_not_called()


# --- the eligibility rule itself, on malformed evidence -------------------------------------------------

PLAN = {"status": "FEASIBLE", "placement": {"c-0": "n1", "c-1": "n2"}, "podsToRecreate": ["c-1"],
        "constraintsDigest": "digest-a"}
COMPONENTS = {"blastBefore": 100.0, "blastAfter": 0.0}


def live_with(entry, *, score=0.8, selected="c", feasible=True, extra_entries=None, plan=PLAN, kind="TOPOLOGY_SPREAD"):
    report = RecommendationReport(
        workflow_id="wf", selected_recommendation_id=selected,
        recommendations=[RecommendationCandidate(id="c", type=kind, service="c", feasible=feasible,
                                                 predicted_blast_radius=0.0, risk_reduction=0.0, resource_cost="LOW",
                                                 confidence=1.0, score=score)])
    candidates = {"c": entry} | (extra_entries or {})
    evidence = {"ranking": {"candidates": candidates}, "failedNode": "w1"}
    if plan is not None:
        evidence["feasibility"] = {"c": plan}
    return LiveRecommendation(report=report, evidence=evidence)


GOOD = {"id": "c", "rank": 1, "score": 0.8, "excluded": None, "components": COMPONENTS}


def test_a_well_formed_top_ranked_candidate_is_eligible_and_selected():
    standing = evaluate_eligibility(live_with(GOOD), "c")

    assert standing.eligible and standing.is_selected and standing.rank == 1 and standing.score == pytest.approx(0.8)


@pytest.mark.parametrize("entry", [
    None, "rank 1", [], {}, {"rank": True, "score": 0.8, "excluded": None}, {"rank": 0, "score": 0.8, "excluded": None},
    {"rank": -1, "score": 0.8, "excluded": None}, {"rank": 1.0, "score": 0.8, "excluded": None},
    {"rank": 1, "score": None, "excluded": None}, {"rank": 1, "score": math.nan, "excluded": None},
    {"rank": 1, "score": math.inf, "excluded": None}, {"rank": 1, "score": "0.8", "excluded": None},
    {"rank": 1, "score": 0.8, "excluded": "NO_PREDICTED_IMPROVEMENT"}, {"rank": None, "score": 0.8, "excluded": None},
    {"rank": 1, "score": 0.0, "excluded": None}, {"rank": 1, "score": -0.2, "excluded": None},
    {"rank": 1, "score": 0.3, "excluded": None},          # the ranking score disagrees with the report's score
])
def test_malformed_or_contradictory_ranking_entries_are_never_eligible(entry):
    assert evaluate_eligibility(live_with(entry), "c").eligible is False


def test_a_boolean_rank_is_not_a_rank_even_when_a_genuine_top_candidate_exists():
    top = {"top": {"id": "top", "rank": 1, "score": 0.9, "excluded": None}}

    standing = evaluate_eligibility(live_with({"id": "c", "rank": True, "score": 0.8, "excluded": None},
                                               selected="top", extra_entries=top), "c")

    assert standing.eligible is False


def test_a_malformed_entry_anywhere_makes_the_whole_evaluation_inconsistent():
    entries = {"top": {"id": "top", "rank": 1, "score": 0.9, "excluded": None},
               "ghost": {"id": "ghost", "rank": True, "score": 0.1, "excluded": None}}

    standing = evaluate_eligibility(live_with({"id": "c", "rank": 2, "score": 0.8, "excluded": None},
                                               selected="top", extra_entries=entries), "c")

    assert standing.eligible is False and "inconsistent" in standing.reason


def test_an_infinite_score_is_never_eligible_even_when_the_report_agrees_with_it():
    entry = {"id": "c", "rank": 1, "score": math.inf, "excluded": None}

    assert evaluate_eligibility(live_with(entry, score=math.inf), "c").eligible is False


def test_a_non_feasible_candidate_or_a_zero_report_score_is_never_eligible():
    assert evaluate_eligibility(live_with(GOOD, feasible=False), "c").eligible is False
    assert evaluate_eligibility(live_with(GOOD, score=0.0), "c").eligible is False


@pytest.mark.parametrize("evidence", [{}, {"ranking": None}, {"ranking": {}}, {"ranking": {"candidates": []}}])
def test_missing_or_malformed_ranking_evidence_is_never_eligible(evidence):
    live = live_with(GOOD)
    assert evaluate_eligibility(LiveRecommendation(report=live.report, evidence=evidence), "c").eligible is False


def test_a_ranked_candidate_that_is_not_first_is_eligible_but_not_selected():
    live = live_with({"id": "c", "rank": 2, "score": 0.8, "excluded": None, "components": COMPONENTS}, selected="top",
                     extra_entries={"top": {"id": "top", "rank": 1, "score": 0.9, "excluded": None}})

    standing = evaluate_eligibility(live, "c")

    assert standing.eligible and not standing.is_selected and standing.selected_recommendation_id == "top"


# --- an approval is bound to the exact proposal: identity, plan and predicted effect ---------------------------------

def fingerprint(**changes):
    plan = dict(PLAN)
    components = dict(COMPONENTS)
    evidence_changes = {}
    for key, value in changes.items():
        if key == "placement":
            plan["placement"] = value
        elif key == "constraintsDigest":
            plan["constraintsDigest"] = value
        elif key == "recreate":
            plan["podsToRecreate"] = value
        elif key in components:
            components[key] = value
        else:
            evidence_changes[key] = value
    live = live_with(dict(GOOD, components=components), plan=plan)
    live.evidence.update(evidence_changes)
    return proposal_fingerprint(live, "c")


def test_the_fingerprint_is_stable_for_the_same_proposal():
    assert fingerprint() == fingerprint() and len(fingerprint()) == 64


@pytest.mark.parametrize("change", [
    {"placement": {"c-0": "n1", "c-1": "n3"}},                  # a pod would go to a different node
    {"placement": {"c-0": "n1", "c-1": "n2", "c-2": "n3"}},     # another pod is part of the plan
    {"recreate": ["c-0", "c-1"]},                               # a different set of pods changes node
    {"blastAfter": 25.0},                                       # a different predicted effect
    {"blastBefore": 50.0},
    {"failedNode": "w2"},                                       # assessed against a different failure
    {"constraintsDigest": "digest-b"},                          # the scheduling constraints it was judged under
])
def test_anything_that_changes_the_plan_or_its_predicted_effect_changes_the_fingerprint(change):
    assert fingerprint(**change) != fingerprint()


def test_a_different_strategy_for_the_same_placement_is_a_different_proposal():
    spread = live_with(GOOD)
    combination = live_with(GOOD, kind="COMBINATION")

    assert proposal_fingerprint(spread, "c") != proposal_fingerprint(combination, "c")


def test_the_same_placement_for_a_different_service_is_a_different_proposal():
    other = live_with(GOOD)
    other.report.recommendations[0] = other.report.recommendations[0].model_copy(update={"service": "elsewhere"})

    assert proposal_fingerprint(other, "c") != proposal_fingerprint(live_with(GOOD), "c")


@pytest.mark.parametrize("digest", [None, "", 7, ["digest-a"]])
def test_a_plan_with_no_usable_constraints_digest_is_not_eligible(digest):
    live = live_with(GOOD, plan=dict(PLAN, constraintsDigest=digest))

    assert proposal_fingerprint(live, "c") is None and evaluate_eligibility(live, "c").eligible is False


def test_a_changed_constraints_digest_makes_a_pending_request_stale(client, source):
    request(client, "top")
    source.set(Spec("top", rank=1, score=0.9, constraints="b"), SECOND)       # same plan, same rank, new constraints

    response = decide(client, "top")

    assert response.status_code == 409 and response.json()["detail"]["code"] == "APPROVAL_PROPOSAL_STALE"


def test_the_fingerprint_ignores_what_does_not_change_the_proposal():
    # The score, the topology snapshot and other candidates move with unrelated cluster activity.
    base = live_with(GOOD)
    noisy = live_with(dict(GOOD, score=0.81), score=0.81, extra_entries={"other": {"rank": 2, "score": 0.4}})
    noisy.evidence["topologySnapshotId"] = "snap-other"
    noisy.evidence["topologyCapturedAt"] = "2030-01-01T00:00:00Z"

    assert proposal_fingerprint(noisy, "c") == proposal_fingerprint(base, "c")


def test_the_members_of_a_combination_are_part_of_its_identity():
    one = live_with(GOOD)
    one.evidence["combinations"] = {"entries": {"c": {"members": ["rec-a", "rec-b"]}}}
    other = live_with(GOOD)
    other.evidence["combinations"] = {"entries": {"c": {"members": ["rec-a", "rec-z"]}}}
    reordered = live_with(GOOD)
    reordered.evidence["combinations"] = {"entries": {"c": {"members": ["rec-b", "rec-a"]}}}

    assert proposal_fingerprint(one, "c") != proposal_fingerprint(other, "c")
    assert proposal_fingerprint(one, "c") == proposal_fingerprint(reordered, "c")


@pytest.mark.parametrize("plan", [None, {}, {"status": "FEASIBLE"}, {"placement": {}}, {"placement": []},
                                  {"placement": {"c-0": "n1"}, "podsToRecreate": "c-0"}])
def test_a_candidate_with_no_recorded_plan_has_no_fingerprint_and_is_not_eligible(plan):
    live = live_with(GOOD, plan=plan)

    assert proposal_fingerprint(live, "c") is None
    assert evaluate_eligibility(live, "c").eligible is False


def test_a_candidate_with_no_recorded_predicted_effect_is_not_eligible():
    live = live_with(dict(GOOD, components={}))

    assert evaluate_eligibility(live, "c").eligible is False


def test_the_request_records_the_fingerprint_of_the_proposal_it_was_made_for(client, source):
    response = request(client, "top")

    proposal = response.json()["evaluationSnapshot"]["proposal"]
    assert len(proposal["fingerprint"]) == 64 and proposal["failedNode"] == "w1"


def test_an_unchanged_proposal_is_approved(client, source):
    request(client, "top")

    assert decide(client, "top").status_code == 200


def test_a_proposal_whose_plan_changed_is_refused_as_stale_even_though_the_candidate_is_still_top_ranked(client, source):
    request(client, "top")
    source.set(Spec("top", rank=1, score=0.9, plan="b"), SECOND)         # same id, same rank: a different plan

    response = decide(client, "top", note="ok")

    assert response.status_code == 409 and response.json()["detail"]["code"] == "APPROVAL_PROPOSAL_STALE"
    assert status_of(client, "top") == "PENDING_APPROVAL"


def test_a_proposal_whose_predicted_effect_changed_is_refused_as_stale(client, source):
    request(client, "top")
    source.set(Spec("top", rank=1, score=0.9, blast_after=40.0), SECOND)

    assert decide(client, "top").status_code == 409


def test_a_score_that_moved_without_the_plan_changing_does_not_make_a_proposal_stale(client, source):
    request(client, "top")
    source.set(Spec("top", rank=1, score=0.7), SECOND)

    assert decide(client, "top").status_code == 200


def test_a_stored_fingerprint_that_was_altered_is_refused(client, source, session):
    from app.shared.persistence.models import RecommendationApprovalRecord

    request(client, "top")
    record = session.query(RecommendationApprovalRecord).filter_by(recommendation_id="top").one()
    record.evaluation_snapshot = {**record.evaluation_snapshot, "proposal": {"fingerprint": "0" * 64}}
    session.commit()

    assert decide(client, "top").status_code == 409


def test_a_verified_request_with_no_stored_fingerprint_predates_the_binding_and_cannot_be_approved(client, source, session):
    ApprovalRepository(session).save(ApprovalModel(
        id=0, recommendation_id="top", proposal_version="v1", status=ApprovalStatus.PENDING_APPROVAL,
        requested_at=datetime.now(timezone.utc), requested_by="developer", recommendation_snapshot={"id": "top"},
        evaluation_snapshot={"provenance": {"source": LIVE_EVIDENCE_SOURCE, "workflowId": "wf1", "namespace": "shop",
                                            "failedNode": "w1"}}))

    response = decide(client, "top")

    assert response.status_code == 409 and response.json()["detail"]["code"] == "APPROVAL_PROPOSAL_STALE"
    assert "predates" in response.json()["detail"]["message"]


def test_a_candidate_that_is_no_longer_eligible_is_reported_as_that_before_it_is_reported_stale(client, source):
    request(client, "top")
    source.set(Spec("top", score=0.0, excluded="NO_PREDICTED_IMPROVEMENT", plan="b"), SECOND, selected=None)

    response = decide(client, "top")

    assert response.status_code == 422 and "not ranked" in response.json()["detail"]


def test_staleness_is_reported_before_a_missing_justification(client, source):
    request(client, "second")
    source.set(TOP, Spec("second", rank=2, score=0.5, plan="b"))

    response = decide(client, "second", note="")

    assert response.status_code == 409 and response.json()["detail"]["code"] == "APPROVAL_PROPOSAL_STALE"


def test_the_decision_records_the_fingerprint_it_was_checked_against(client, source):
    request(client, "top")

    check = decide(client, "top").json()["evaluationSnapshot"]["approvalCheck"]

    assert check["proposalFingerprint"] == client.get("/api/recommendations/top/approval").json()[
        "evaluationSnapshot"]["proposal"]["fingerprint"]


def test_a_combination_is_bound_by_its_members_as_well_as_its_plan(client, source):
    combo = Spec("combo", rank=1, score=0.8, members=("rec-a", "rec-b"))
    source.set(combo)
    request(client, "combo")
    source.set(Spec("combo", rank=1, score=0.8, members=("rec-a", "rec-c")))        # same plan, different members

    assert decide(client, "combo").status_code == 409


# --- the YAML stored with a request is bound too -----------------------------------------------------------------

def test_the_request_records_a_hash_of_the_yaml_it_stores(client, source):
    import hashlib

    response = request(client, "top", yamlSnapshot="kind: Deployment\n")

    assert response.json()["evaluationSnapshot"]["proposal"]["yaml"]["sha256"] == hashlib.sha256(b"kind: Deployment\n").hexdigest()
    assert request(client, "second").json()["evaluationSnapshot"]["proposal"]["yaml"]["sha256"] is None


def test_yaml_that_changed_after_the_request_is_refused_as_stale(client, source, session):
    from app.shared.persistence.models import RecommendationApprovalRecord

    request(client, "top", yamlSnapshot="kind: Deployment\n")
    record = session.query(RecommendationApprovalRecord).filter_by(recommendation_id="top").one()
    record.yaml_snapshot = "kind: Deployment\nreplicas: 99\n"
    session.commit()

    response = decide(client, "top")

    assert response.status_code == 409 and response.json()["detail"]["code"] == "APPROVAL_PROPOSAL_STALE"
    assert "YAML" in response.json()["detail"]["message"]


def test_unchanged_yaml_is_approved(client, source):
    request(client, "top", yamlSnapshot="kind: Deployment\n")

    assert decide(client, "top").status_code == 200


def test_the_yaml_hash_is_labelled_an_integrity_check_of_client_supplied_text_not_a_verification(client, source):
    response = request(client, "top", yamlSnapshot="kind: Deployment\n")

    recorded = response.json()["evaluationSnapshot"]["proposal"]["yaml"]
    assert recorded["origin"] == "client-supplied" and recorded["verifiedAgainstRecommendation"] is False


def test_yaml_unrelated_to_the_recommendation_is_accepted_but_never_marked_as_verified_and_nothing_is_applied(client, source):
    # Nothing compares the YAML with the recommendation. This documents that, so it cannot be mistaken for a check.
    request(client, "top", yamlSnapshot="apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: unrelated\n")

    approved = decide(client, "top")

    check = approved.json()["evaluationSnapshot"]["approvalCheck"]
    assert approved.status_code == 200
    assert check["yamlVerifiedAgainstRecommendation"] is False and check["appliesNothing"] is True
    assert approved.json()["evaluationSnapshot"]["proposal"]["yaml"]["verifiedAgainstRecommendation"] is False


def test_the_stale_yaml_message_says_it_is_an_integrity_check_only(client, source, session):
    from app.shared.persistence.models import RecommendationApprovalRecord

    request(client, "top", yamlSnapshot="kind: Deployment\n")
    record = session.query(RecommendationApprovalRecord).filter_by(recommendation_id="top").one()
    record.yaml_snapshot = "kind: Deployment\nreplicas: 99\n"
    session.commit()

    message = decide(client, "top").json()["detail"]["message"]

    assert "integrity check only" in message and "not verified against the recommendation" in message
