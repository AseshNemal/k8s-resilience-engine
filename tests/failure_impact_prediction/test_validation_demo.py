import json

import pytest

from app.engine.failure_impact_prediction.validation_demo import (
    SYNTHETIC_EVIDENCE_DISCLAIMER,
    SYNTHETIC_EVIDENCE_STATUS,
    SYNTHETIC_OBSERVATION_SOURCE,
    build_synthetic_validation_demo,
)


def test_demo_is_explicitly_synthetic_and_json_serializable() -> None:
    demo = build_synthetic_validation_demo()

    assert json.loads(json.dumps(demo)) == demo
    assert demo["evidenceStatus"] == SYNTHETIC_EVIDENCE_STATUS
    assert demo["disclaimer"] == SYNTHETIC_EVIDENCE_DISCLAIMER
    assert "not results" in SYNTHETIC_EVIDENCE_DISCLAIMER


def test_demo_exercises_exact_and_mismatched_outcomes() -> None:
    demo = build_synthetic_validation_demo()
    validation = demo["validation"]

    assert isinstance(validation, dict)
    assert validation["batchId"] == "synthetic-node-1-validation"
    assert validation["totalExperiments"] == 3
    assert validation["exactMatchCount"] == 1
    assert validation["exactMatchRate"] == pytest.approx(1 / 3)
    assert validation["serviceMismatchExperimentIds"] == [
        "synthetic-missed-service"
    ]
    assert validation["workflowMismatchExperimentIds"] == [
        "synthetic-overpredicted-workflow"
    ]
    assert validation["serviceMetrics"] == {
        "truePositives": 3,
        "falsePositives": 0,
        "falseNegatives": 1,
        "precision": 1.0,
        "recall": 0.75,
        "f1Score": pytest.approx(6 / 7),
    }
    assert validation["workflowMetrics"] == {
        "truePositives": 2,
        "falsePositives": 1,
        "falseNegatives": 0,
        "precision": pytest.approx(2 / 3),
        "recall": 1.0,
        "f1Score": 0.8,
    }


def test_every_demo_experiment_identifies_assumed_evidence() -> None:
    demo = build_synthetic_validation_demo()
    validation = demo["validation"]

    assert isinstance(validation, dict)
    experiments = validation["experiments"]
    assert isinstance(experiments, list)
    assert experiments
    assert all(
        experiment["observationSource"] == SYNTHETIC_OBSERVATION_SOURCE
        for experiment in experiments
    )
