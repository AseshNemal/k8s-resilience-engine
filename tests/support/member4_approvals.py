"""Builds an APPROVED approval from an offline scenario, for tests of what happens AFTER approval.

TEST-ONLY, and the one place that fabricates verified provenance. It relabels an offline evaluation's evidence source
as the live one and walks it through the real ``ApprovalService`` (request, then approve), so the validation tests start
from an approval the real gate produced for that evaluation. The relabelled evaluation never leaves the test: it is
not a recommendation the page can show, and the product never imports this module.
"""

import dataclasses

from app.engine.recommendation_evaluation_validation.approval import (
    LIVE_EVIDENCE_SOURCE,
    ApprovalModel,
    ApprovalRepository,
    ApprovalService,
    LiveRecommendation,
)
from app.engine.recommendation_evaluation_validation.yaml_proposal import GENERATED, build_yaml_proposal
from tests.support.member4_harness import Scenario


class InMemoryApprovals(ApprovalRepository):
    """One approval per recommendation in memory: the real service without a database."""

    def __init__(self) -> None:  # noqa: D107
        self.items: dict[str, ApprovalModel] = {}

    def get_by_recommendation_id(self, recommendation_id):
        return self.items.get(recommendation_id)

    def save(self, approval):
        approval.id = len(self.items) + 1
        self.items[approval.recommendation_id] = approval
        return approval

    def update(self, approval):
        self.items[approval.recommendation_id] = approval
        return approval


def as_live(live: LiveRecommendation) -> LiveRecommendation:
    """The same evaluation labelled as if it had been computed from the connected cluster (test-only)."""
    return dataclasses.replace(live, evidence={**live.evidence, "source": LIVE_EVIDENCE_SOURCE})


def requested_for_test(scenario: Scenario, **evaluate_options) -> tuple[ApprovalService, LiveRecommendation, str]:
    """A PENDING request for the scenario's selected candidate, made through the real service."""
    live = as_live(scenario.evaluate(**evaluate_options))
    recommendation_id = live.report.selected_recommendation_id
    proposal = build_yaml_proposal(live, recommendation_id)
    assert proposal.status == GENERATED, proposal.reason
    service = ApprovalService(InMemoryApprovals())
    service.request_approval(recommendation_id, "v1", live.report, requested_by="test", evidence=live.evidence,
                             proposal_yaml=proposal.yaml)
    return service, live, recommendation_id


def approved_for_test(scenario: Scenario, **evaluate_options) -> tuple[ApprovalModel, LiveRecommendation]:
    """An APPROVED approval for the scenario's selected candidate, and the evaluation it was approved against."""
    service, live, recommendation_id = requested_for_test(scenario, **evaluate_options)
    approval = service.approve(recommendation_id, "v1", "", decided_by="test", evaluate=lambda _: live)
    return approval, live
