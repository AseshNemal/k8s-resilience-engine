"""Confidence for dependency labels, checked against hand-computed values."""

from types import SimpleNamespace

import pytest

from app.engine.workflow_dependency_modeling import label_statistics as stats
from app.engine.workflow_dependency_modeling.dependency_assessment_models import AssessmentPolicy


def cohort(successes, eligible):
    return SimpleNamespace(succeeded_records=successes, eligible_records=eligible)


@pytest.fixture(autouse=True)
def default_level(monkeypatch):
    monkeypatch.delenv(stats.CONFIDENCE_SETTING, raising=False)


def test_wilson_and_fisher_match_hand_computed_values():
    # Wilson 95%: 5/5 → lower 0.5655; 0/5 → upper z²/(n+z²) = 3.8415/8.8415 = 0.4345.
    assert stats.wilson_interval(5, 5, 0.95) == (pytest.approx(0.5655, abs=1e-4), pytest.approx(1.0))
    assert stats.wilson_interval(0, 5, 0.95)[1] == pytest.approx(0.4345, abs=1e-4)
    # Newcombe: 1 − sqrt(0.4345² + 0.4345²) = 0.3855.
    assert stats.newcombe_interval(5, 5, 0, 5, 0.95)[0] == pytest.approx(0.3855, abs=1e-4)
    # Only one table is at least as extreme: C(5,5)·C(5,0)/C(10,5) = 1/252.
    assert stats.fisher_one_sided(5, 5, 0, 5) == pytest.approx(1 / 252)
    assert stats.wilson_interval(0, 0, 0.95) is None


def test_a_mandatory_label_from_five_runs_is_not_yet_supported_and_says_how_many_runs_would_do():
    result = stats.label_statistics("mandatory", cohort(5, 5), cohort(0, 5), AssessmentPolicy())
    assert result["supportedAtConfidence"] is False
    # 0/n upper bound ≤ 0.2 needs 3.8415/(n+3.8415) ≤ 0.2 → n ≥ 15.4.
    assert result["runsNeededPerCohort"] == 16
    assert "not yet supported" in result["verdict"]


def test_enough_runs_support_the_label():
    result = stats.label_statistics("mandatory", cohort(16, 16), cohort(0, 16), AssessmentPolicy())
    assert result["supportedAtConfidence"] is True
    assert result["runsNeededPerCohort"] is None
    assert all(check.endswith("yes") for check in result["checks"])


def test_unresolved_labels_get_intervals_but_no_verdict():
    result = stats.label_statistics("insufficient_evidence", cohort(3, 5), cohort(2, 5), AssessmentPolicy())
    assert result["supportedAtConfidence"] is None and result["effectInterval"] is not None


def test_the_confidence_level_is_a_backend_setting(monkeypatch):
    monkeypatch.setenv(stats.CONFIDENCE_SETTING, "0.9")
    assert stats.label_statistics("optional", cohort(5, 5), cohort(5, 5), AssessmentPolicy())["confidenceLevel"] == 0.9
    monkeypatch.setenv(stats.CONFIDENCE_SETTING, "2")
    with pytest.raises(ValueError):
        stats.confidence_level()
