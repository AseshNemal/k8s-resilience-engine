import json
from datetime import datetime, timedelta, timezone

import pytest

from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
    build_prediction_validation_record,
)
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.failure_impact_prediction.validation_export import (
    build_validation_summary_export,
)
from app.engine.failure_impact_prediction.validation_summary import (
    PredictionValidationSummary,
    build_prediction_validation_summary,
)


def _summary(
    *,
    failed_node: str = "node-1",
    failed_services: tuple[str, ...] = ("cartservice",),
    failed_workflows: tuple[str, ...] = ("checkout",),
) -> PredictionValidationSummary:
    workflow, deployment = get_demo_inputs()
    prediction = predict_failure_impact(workflow, deployment, failed_node)
    observation = ObservedFailureOutcome(
        experiment_id="experiment-1",
        failed_node=failed_node,
        failed_services=failed_services,
        failed_workflows=failed_workflows,
    )
    record = build_prediction_validation_record(
        prediction,
        observation,
        observation_source="controlled node shutdown",
        observed_at=datetime(
            2026,
            10,
            20,
            10,
            30,
            tzinfo=timezone(timedelta(hours=5, minutes=30)),
        ),
        workflow_freeze_id="checkout-freeze-v1",
        deployment_snapshot_id="deployment-before-node-failure",
        namespace="research",
    )
    return build_prediction_validation_summary("node-failure-batch", [record])


def test_export_is_json_serializable_and_preserves_evidence() -> None:
    exported = build_validation_summary_export(_summary())

    assert json.loads(json.dumps(exported)) == exported
    assert exported["batchId"] == "node-failure-batch"
    assert exported["totalExperiments"] == 1
    assert exported["exactMatchCount"] == 1
    assert exported["exactMatchRate"] == pytest.approx(1.0)
    assert exported["serviceMetrics"] == {
        "truePositives": 1,
        "falsePositives": 0,
        "falseNegatives": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1Score": 1.0,
    }
    assert exported["workflowMetrics"] == {
        "truePositives": 1,
        "falsePositives": 0,
        "falseNegatives": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1Score": 1.0,
    }
    assert exported["experiments"] == [
        {
            "experimentId": "experiment-1",
            "workflowId": "checkout",
            "failedNode": "node-1",
            "observationSource": "controlled node shutdown",
            "observedAt": "2026-10-20T05:00:00Z",
            "workflowFreezeId": "checkout-freeze-v1",
            "deploymentSnapshotId": "deployment-before-node-failure",
            "namespace": "research",
            "predictedFailedServices": ["cartservice"],
            "observedFailedServices": ["cartservice"],
            "serviceTruePositives": ["cartservice"],
            "serviceFalsePositives": [],
            "serviceFalseNegatives": [],
            "predictedFailedWorkflows": ["checkout"],
            "observedFailedWorkflows": ["checkout"],
            "workflowTruePositives": ["checkout"],
            "workflowFalsePositives": [],
            "workflowFalseNegatives": [],
            "servicesMatch": True,
            "workflowsMatch": True,
            "exactMatch": True,
        }
    ]


def test_export_preserves_undefined_metrics_as_json_null() -> None:
    summary = _summary(
        failed_node="node-2",
        failed_services=(),
        failed_workflows=(),
    )

    exported = build_validation_summary_export(summary)

    service_metrics = exported["serviceMetrics"]
    workflow_metrics = exported["workflowMetrics"]
    assert isinstance(service_metrics, dict)
    assert isinstance(workflow_metrics, dict)
    assert service_metrics["precision"] is None
    assert service_metrics["recall"] is None
    assert service_metrics["f1Score"] is None
    assert workflow_metrics["precision"] is None
    assert workflow_metrics["recall"] is None
    assert workflow_metrics["f1Score"] is None
    assert '"precision": null' in json.dumps(exported)


def test_export_rejects_summary_with_mismatched_metric_identity() -> None:
    summary = _summary()
    invalid_metrics = summary.metrics.__class__(
        batch_id="different-batch",
        total_experiments=summary.metrics.total_experiments,
        exact_match_count=summary.metrics.exact_match_count,
        exact_match_rate=summary.metrics.exact_match_rate,
        service_metrics=summary.metrics.service_metrics,
        workflow_metrics=summary.metrics.workflow_metrics,
    )
    invalid_summary = PredictionValidationSummary(
        batch=summary.batch,
        metrics=invalid_metrics,
    )

    with pytest.raises(ValueError, match="same batch_id"):
        build_validation_summary_export(invalid_summary)
