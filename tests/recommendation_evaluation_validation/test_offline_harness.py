"""The offline harness runs Member 4's whole evaluation on controlled artifacts, and can never pass as live evidence."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.engine.recommendation_evaluation_validation.approval import (
    LIVE_EVIDENCE_SOURCE,
    ApprovalModel,
    ApprovalRepository,
    ApprovalService,
    UnverifiedProvenanceError,
    has_verified_provenance,
)
from app.shared.schemas.approval import ApprovalStatus
from tests.support.member4_harness import OFFLINE_SOURCE, bookinfo_scenario

APP = Path(__file__).resolve().parents[2] / "app"


def test_the_offline_source_is_not_the_live_source():
    assert OFFLINE_SOURCE != LIVE_EVIDENCE_SOURCE


def test_concentrated_bookinfo_gets_a_feasible_ranked_spread_for_reviews():
    live = bookinfo_scenario("concentrated").evaluate()

    by_service = {c.service: c for c in live.report.recommendations}
    # reviews and ratings are both on w1; each is a concentration root cause, productpage/details are spread.
    assert set(by_service) == {"reviews", "ratings"}
    assert by_service["reviews"].feasible and by_service["reviews"].type == "TOPOLOGY_SPREAD"
    assert live.evidence["failedNode"] == "w1"
    assert live.evidence["blastRadiusBefore"] == pytest.approx(100.0)
    assert live.report.selected_recommendation_id is not None


def test_a_distributed_bookinfo_has_nothing_to_recommend():
    live = bookinfo_scenario("distributed").evaluate()

    assert live.report.recommendations == []
    assert live.report.selected_recommendation_id is None
    assert live.evidence["blastRadiusBefore"] == pytest.approx(0.0)


def test_every_evaluation_is_labelled_offline_and_a_prediction():
    live = bookinfo_scenario("concentrated").evaluate()

    assert live.evidence["source"] == OFFLINE_SOURCE
    assert live.evidence["isPrediction"] is True
    assert live.evidence["topologyStatus"] == "OFFLINE_FIXTURE"


def test_the_inputs_are_not_changed_by_an_evaluation():
    scenario = bookinfo_scenario("concentrated")
    before = scenario.deployment.model_dump()

    scenario.evaluate()

    assert scenario.deployment.model_dump() == before


def test_harness_evidence_does_not_count_as_verified_provenance():
    live = bookinfo_scenario("concentrated").evaluate()
    approval = ApprovalModel(
        id=1, recommendation_id="x", proposal_version="v1", status=ApprovalStatus.PENDING_APPROVAL,
        requested_at=datetime.now(timezone.utc), requested_by="t", recommendation_snapshot={},
        evaluation_snapshot={"provenance": dict(live.evidence)})

    assert has_verified_provenance(approval) is False


class _OneApproval(ApprovalRepository):
    """Holds a single approval in memory, so the gate can be exercised without a database."""

    def __init__(self, approval):  # noqa: D107 (no session needed)
        self.approval = approval

    def get_by_recommendation_id(self, recommendation_id):
        return self.approval

    def update(self, approval):
        self.approval = approval
        return approval


def test_the_approval_gate_refuses_a_harness_recommendation():
    scenario = bookinfo_scenario("concentrated")
    live = scenario.evaluate()
    chosen = live.report.selected_recommendation_id
    approval = ApprovalModel(
        id=1, recommendation_id=chosen, proposal_version="v1", status=ApprovalStatus.PENDING_APPROVAL,
        requested_at=datetime.now(timezone.utc), requested_by="t", recommendation_snapshot={},
        evaluation_snapshot={"provenance": dict(live.evidence)})

    with pytest.raises(UnverifiedProvenanceError):
        ApprovalService(_OneApproval(approval)).approve(chosen, "v1", "looks fine", evaluate=lambda a: live)


def test_the_product_never_imports_the_harness():
    offenders = [str(path.relative_to(APP)) for path in APP.rglob("*.py")
                 if "member4_harness" in path.read_text(encoding="utf-8")]

    assert offenders == []
