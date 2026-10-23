"""The research approval tier: a declared workflow can be approved for research validation, and for nothing else.

Every guard of the production gate still applies. What these tests pin is the four deliberate differences, and that the tier cannot
be reached from, or used to bypass, the production gate.
"""

import dataclasses
import json

import pytest

from app.engine.recommendation_evaluation_validation import live_pipeline
from app.engine.recommendation_evaluation_validation.approval import (
    DECLARED_WORKFLOW_SOURCE,
    RESEARCH_TIER,
    ApprovalService,
    PreconditionError,
    ResearchApprovalService,
    StaleProposalError,
    UnverifiedProvenanceError,
    has_research_provenance,
    has_verified_provenance,
)
from app.engine.recommendation_evaluation_validation.approval.research import ResearchProvenanceError
from app.engine.recommendation_evaluation_validation.declared_workflow import ENV_NAME, get_declared_workflow
from app.engine.recommendation_evaluation_validation.validation import (
    TestTarget,
    ValidationPlan,
    ValidationRefusedError,
)
from app.engine.recommendation_evaluation_validation.yaml_proposal import GENERATED, build_yaml_proposal
from app.shared.schemas.approval import ApprovalStatus
from app.shared.schemas.workflow import WorkflowGraph
from tests.recommendation_evaluation_validation.test_independent_inputs import (  # noqa: F401  (fixtures and helpers)
    DECLARATION,
    concentrated_db,
    declared,
    freeze_checkout,
    no_frozen_workflow,
)
from tests.support.member4_approvals import InMemoryApprovals

ACK = "I understand this workflow is declared by me, not observed, and approve it for research validation only."
TARGET = TestTarget("kind-test", "test")


def evaluate_declared():
    return live_pipeline.build_declared_recommendation("checkout", None, "shop")


@pytest.fixture
def world(use_cluster, declared, no_frozen_workflow):
    use_cluster(concentrated_db())
    return evaluate_declared()


def requested(service, live, **overrides):
    selected = live.report.selected_recommendation_id
    options = dict(acknowledged_by="asesh", acknowledgement=ACK, proposal_yaml=build_yaml_proposal(live, selected).yaml)
    return selected, service.request_research_approval(selected, "v1", live, **{**options, **overrides})


def approved(service, live):
    selected, _ = requested(service, live)
    return service.approve(selected, "v1", "", decided_by="asesh", evaluate=lambda _approval: evaluate_declared())


# --- requesting ------------------------------------------------------------------------------------------------------------------------

def test_a_request_keeps_the_acknowledgement_the_digest_the_scope_and_the_server_generated_yaml(world):
    service = ResearchApprovalService(InMemoryApprovals())

    selected, approval = requested(service, world)

    provenance = approval.evaluation_snapshot["provenance"]
    assert approval.status is ApprovalStatus.PENDING_APPROVAL and approval.requested_by == "asesh"
    assert provenance["source"] == DECLARED_WORKFLOW_SOURCE and provenance["researchTier"] == RESEARCH_TIER
    assert provenance["researchAcknowledgement"]["by"] == "asesh" and provenance["researchAcknowledgement"]["statement"] == ACK
    assert "research cluster only" in provenance["researchAcknowledgement"]["scope"]
    assert len(provenance["declaredWorkflow"]["declarationSha256"]) == 64
    assert approval.evaluation_snapshot["proposal"]["yaml"]["origin"] == "server-generated"
    assert has_research_provenance(approval) is True and has_verified_provenance(approval) is False


@pytest.mark.parametrize("override", [dict(acknowledged_by=""), dict(acknowledged_by="  "), dict(acknowledged_by="developer"),
                                      dict(acknowledgement=""), dict(acknowledgement="ok"), dict(proposal_yaml=None)])
def test_a_request_needs_a_named_person_a_real_acknowledgement_and_server_yaml(world, override):
    with pytest.raises(PreconditionError):
        requested(ResearchApprovalService(InMemoryApprovals()), world, **override)


def test_a_live_evaluation_cannot_use_the_research_tier(monkeypatch, use_cluster, declared):
    use_cluster(concentrated_db())
    freeze_checkout(monkeypatch)
    live = live_pipeline.build_live_recommendation("checkout", None, "shop")

    with pytest.raises(PreconditionError) as raised:
        requested(ResearchApprovalService(InMemoryApprovals()), live)

    assert "declared workflow" in str(raised.value)


def test_a_candidate_that_is_not_verified_feasible_and_ranked_is_refused_here_as_everywhere(use_cluster, declared, no_frozen_workflow):
    use_cluster(concentrated_db(requests=None))          # containers never read: INSUFFICIENT_EVIDENCE
    live = evaluate_declared()
    candidate = live.report.recommendations[0].id

    with pytest.raises(PreconditionError):
        ResearchApprovalService(InMemoryApprovals()).request_research_approval(
            candidate, "v1", live, acknowledged_by="asesh", acknowledgement=ACK, proposal_yaml="anything")


# --- deciding ---------------------------------------------------------------------------------------------------------------------------

def test_approving_re_evaluates_checks_everything_the_production_gate_checks_and_names_the_person(world):
    service = ResearchApprovalService(InMemoryApprovals())

    result = approved(service, world)

    assert result.status is ApprovalStatus.APPROVED and result.decided_by == "asesh"
    check = result.evaluation_snapshot["approvalCheck"]
    assert check["yamlVerifiedAgainstRecommendation"] is True and check["appliesNothing"] is True
    assert has_research_provenance(result) is True


def test_an_anonymous_decision_is_refused(world):
    service = ResearchApprovalService(InMemoryApprovals())
    selected, _ = requested(service, world)

    for decider in ("", "developer"):
        with pytest.raises(PreconditionError):
            service.approve(selected, "v1", "", decided_by=decider, evaluate=lambda _a: evaluate_declared())


def test_a_declaration_that_changed_since_the_request_makes_the_approval_stale(world, declared):
    service = ResearchApprovalService(InMemoryApprovals())
    selected, _ = requested(service, world)
    document = json.loads(declared.read_text())
    declared.write_text(json.dumps({**document, "criticality": 0.5}))          # a different file: a different digest

    with pytest.raises(StaleProposalError) as raised:
        service.approve(selected, "v1", "", decided_by="asesh", evaluate=lambda _a: evaluate_declared())

    assert "declaration" in str(raised.value)


def test_it_cannot_be_approved_against_an_evaluation_that_is_no_longer_declared(monkeypatch, use_cluster, world, declared):
    service = ResearchApprovalService(InMemoryApprovals())
    selected, _ = requested(service, world)
    freeze_checkout(monkeypatch)

    with pytest.raises(StaleProposalError):
        service.approve(selected, "v1", "", decided_by="asesh",
                        evaluate=lambda _a: live_pipeline.build_live_recommendation("checkout", None, "shop"))


# --- it cannot be used to bypass the production gate, nor the other way round ---------------------------------------------------------

def test_the_production_gate_still_refuses_a_research_approval(world):
    repository = InMemoryApprovals()
    selected, _ = requested(ResearchApprovalService(repository), world)

    with pytest.raises(UnverifiedProvenanceError):
        ApprovalService(repository).approve(selected, "v1", "", decided_by="asesh", evaluate=lambda _a: evaluate_declared())


def test_the_research_gate_refuses_a_production_approval(monkeypatch, use_cluster, declared):
    use_cluster(concentrated_db())
    freeze_checkout(monkeypatch)
    live = live_pipeline.build_live_recommendation("checkout", None, "shop")
    repository = InMemoryApprovals()
    selected = live.report.selected_recommendation_id
    ApprovalService(repository).request_approval(selected, "v1", live.report, requested_by="t", evidence=live.evidence,
                                                 proposal_yaml=build_yaml_proposal(live, selected).yaml)

    with pytest.raises(ResearchProvenanceError):
        ResearchApprovalService(repository).approve(selected, "v1", "", decided_by="asesh", evaluate=lambda _a: live)


def test_the_production_defaults_are_exactly_what_they_were():
    assert ApprovalService.require_applicable_yaml is True and ResearchApprovalService.require_applicable_yaml is False


@pytest.mark.parametrize("drop", ["researchTier", "researchAcknowledgement", "declaredWorkflow"])
def test_a_declared_record_missing_any_part_of_the_research_provenance_is_not_research_provenance(world, drop):
    service = ResearchApprovalService(InMemoryApprovals())
    _, approval = requested(service, world)
    provenance = {k: v for k, v in approval.evaluation_snapshot["provenance"].items() if k != drop}
    stripped = dataclasses.replace(approval, evaluation_snapshot={**approval.evaluation_snapshot, "provenance": provenance})

    assert has_research_provenance(stripped) is False


def test_a_record_whose_yaml_was_client_supplied_is_not_research_provenance(world):
    _, approval = requested(ResearchApprovalService(InMemoryApprovals()), world)
    snapshot = json.loads(json.dumps(approval.evaluation_snapshot))
    snapshot["proposal"]["yaml"]["origin"] = "client-supplied"

    assert has_research_provenance(dataclasses.replace(approval, evaluation_snapshot=snapshot)) is False


# --- validation: opt-in, bound to the declaration, and labelled ----------------------------------------------------------------------------

def plan_for(world, **options):
    approval = approved(ResearchApprovalService(InMemoryApprovals()), world)
    return approval, ValidationPlan.from_approval(approval, evaluate_declared(), get_declared_workflow("checkout").graph, TARGET, **options)


def test_a_validation_plan_ignores_a_research_approval_unless_it_is_asked_to_honour_it(world):
    approval = approved(ResearchApprovalService(InMemoryApprovals()), world)

    with pytest.raises(ValidationRefusedError) as raised:
        ValidationPlan.from_approval(approval, evaluate_declared(), get_declared_workflow("checkout").graph, TARGET)

    assert raised.value.code == "APPROVAL_UNVERIFIED"


def test_with_the_opt_in_the_plan_is_built_and_says_its_workflow_was_declared(world):
    approval, plan = plan_for(world, allow_declared_workflow=True)

    assert plan.workflow_basis == "DECLARED" and plan.declared is True
    assert plan.declaration["declarationSha256"] == approval.evaluation_snapshot["provenance"]["declaredWorkflow"]["declarationSha256"]
    assert plan.research_acknowledgement["by"] == "asesh"
    assert plan.predicted.before.blast_radius == pytest.approx(100.0)


def test_the_opt_in_never_makes_a_non_test_target_writable_or_an_unapproved_request_valid(world):
    service = ResearchApprovalService(InMemoryApprovals())
    selected, pending = requested(service, world)

    with pytest.raises(ValidationRefusedError) as raised:
        ValidationPlan.from_approval(pending, evaluate_declared(), get_declared_workflow("checkout").graph, TARGET,
                                     allow_declared_workflow=True)
    assert raised.value.code == "APPROVAL_NOT_APPROVED"
    done = approved(ResearchApprovalService(InMemoryApprovals()), world)
    with pytest.raises(ValidationRefusedError) as raised:
        ValidationPlan.from_approval(done, evaluate_declared(), get_declared_workflow("checkout").graph,
                                     TestTarget("prod", "production"), allow_declared_workflow=True)
    assert raised.value.code == "TARGET_NOT_TEST_ENVIRONMENT"


def test_a_plan_is_refused_when_the_declaration_changed_after_the_approval(world, declared):
    approval = approved(ResearchApprovalService(InMemoryApprovals()), world)
    document = json.loads(declared.read_text())
    declared.write_text(json.dumps({**document, "criticality": 0.4}))

    with pytest.raises(ValidationRefusedError) as raised:
        ValidationPlan.from_approval(approval, evaluate_declared(), get_declared_workflow("checkout").graph, TARGET,
                                     allow_declared_workflow=True)

    assert raised.value.code == "APPROVAL_STALE" and "declaration" in raised.value.message


def test_a_plan_is_refused_when_the_workflow_handed_in_is_not_the_declared_one(world):
    approval = approved(ResearchApprovalService(InMemoryApprovals()), world)
    other = WorkflowGraph(workflow_id="checkout", criticality=0.9, mandatory_services=["web"], edges=[])   # db is no longer needed

    with pytest.raises(ValidationRefusedError) as raised:
        ValidationPlan.from_approval(approval, evaluate_declared(), other, TARGET, allow_declared_workflow=True)

    assert raised.value.code == "APPROVAL_STALE" and "not the declared workflow" in raised.value.message


def test_a_frozen_workflow_plan_is_unchanged_and_says_it_is_frozen():
    from tests.support.member4_approvals import approved_for_test
    from tests.support.member4_harness import bookinfo_scenario

    scenario = bookinfo_scenario("concentrated")
    approval, live = approved_for_test(scenario)

    plan = ValidationPlan.from_approval(approval, live, scenario.workflow, TARGET)

    assert plan.workflow_basis == "FROZEN" and plan.declared is False and plan.declaration is None


# --- tier separation: neither gate can decide, or hide, the other's records ------------------------------------------------------------

def production_record(monkeypatch, repository):
    freeze_checkout(monkeypatch)
    live = live_pipeline.build_live_recommendation("checkout", None, "shop")
    selected = live.report.selected_recommendation_id
    ApprovalService(repository).request_approval(selected, "v1", live.report, requested_by="t", evidence=live.evidence,
                                                 proposal_yaml=build_yaml_proposal(live, selected).yaml)
    return selected


def test_the_research_gate_cannot_reject_a_production_record(monkeypatch, use_cluster, declared):
    use_cluster(concentrated_db())
    repository = InMemoryApprovals()
    selected = production_record(monkeypatch, repository)

    with pytest.raises(ResearchProvenanceError):
        ResearchApprovalService(repository).reject(selected, "v1", "no", decided_by="asesh")

    assert repository.get_by_recommendation_id(selected).status is ApprovalStatus.PENDING_APPROVAL      # untouched


@pytest.mark.parametrize("decider", ["", "  ", "developer"])
def test_a_research_rejection_needs_a_named_person(world, decider):
    service = ResearchApprovalService(InMemoryApprovals())
    selected, _ = requested(service, world)

    with pytest.raises(PreconditionError):
        service.reject(selected, "v1", "no", decided_by=decider)


def test_a_named_person_can_reject_a_research_request(world):
    service = ResearchApprovalService(InMemoryApprovals())
    selected, _ = requested(service, world)

    rejected = service.reject(selected, "v1", "not now", decided_by="asesh")

    assert rejected.status is ApprovalStatus.REJECTED and rejected.decided_by == "asesh"


@pytest.mark.parametrize("decide", [False, True])
def test_a_research_request_cannot_shadow_an_existing_production_record(monkeypatch, use_cluster, declared, decide):
    use_cluster(concentrated_db())
    repository = InMemoryApprovals()
    selected = production_record(monkeypatch, repository)
    if decide:
        ApprovalService(repository).reject(selected, "v1", "n", decided_by="t")        # even a decided production record is not hidden
    live = evaluate_declared()

    with pytest.raises(PreconditionError) as raised:
        requested(ResearchApprovalService(repository), live)

    assert "not a research-tier one" in str(raised.value)
    assert has_verified_provenance(repository.get_by_recommendation_id(selected)) is True       # the production record is still what a gate sees


def test_a_second_research_request_after_a_decided_research_one_is_allowed(world):
    service = ResearchApprovalService(InMemoryApprovals())
    selected, _ = requested(service, world)
    service.reject(selected, "v1", "not now", decided_by="asesh")

    again = requested(service, world)[1]

    assert again.status is ApprovalStatus.PENDING_APPROVAL and has_research_provenance(again)
