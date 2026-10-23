"""The optional ``ranking_weights`` parameter (added for the offline lab) must leave the default ranking untouched."""

import pytest

from app.engine.recommendation_evaluation_validation.ranking import RankingWeights
from tests.support.member4_scenarios import catalogue

CASES = catalogue()


def snapshot(live):
    return live.report.model_dump(), live.evidence["ranking"], live.details


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_no_weights_none_and_the_default_weights_give_identical_evaluations(case):
    implicit = snapshot(case.scenario.evaluate())
    explicit_none = snapshot(case.scenario.evaluate(ranking_weights=None))
    explicit_default = snapshot(case.scenario.evaluate(ranking_weights=RankingWeights()))

    assert implicit == explicit_none == explicit_default


def test_the_weights_that_were_used_are_reported_and_a_custom_set_is_actually_applied():
    case = next(c for c in CASES if c.name == "concentrated-replicas")
    custom = RankingWeights(target_share=0.5, disruption_discount=0.1, resource_discount=0.2)

    default_weights = case.scenario.evaluate().evidence["ranking"]["weights"]
    custom_weights = case.scenario.evaluate(ranking_weights=custom).evidence["ranking"]["weights"]

    assert default_weights["targetShare"] == pytest.approx(0.7)
    assert custom_weights["targetShare"] == pytest.approx(0.5) and custom_weights["disruptionDiscount"] == pytest.approx(0.1)
