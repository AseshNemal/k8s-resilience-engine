from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.engine.failure_impact_prediction.captured_evidence_combiner import (
    IncompleteCapturedExperimentError,
)
from app.engine.failure_impact_prediction.validation_evidence import (
    assess_validation_evidence,
)
from app.engine.failure_impact_prediction.validation_manifest import (
    parse_captured_experiment_manifest,
    require_complete_manifest_evidence,
)


def _payload() -> dict[str, object]:
    return {
        "experimentId": "worker-1-failure",
        "failedNode": "resilience-research-worker",
        "observationSource": "connected-cluster experiment manifest",
        "observedAt": "2026-10-21T10:30:00+05:30",
        "workflowFreezeId": "checkout-freeze-v1",
        "deploymentSnapshotId": "concentrated-baseline",
        "namespace": "research",
        "expectedServices": ["cartservice", "paymentservice"],
        "serviceResults": {
            "cartservice": "SERVICE_AVAILABLE",
            "paymentservice": "SERVICE_UNAVAILABLE",
        },
        "expectedWorkflows": ["checkout"],
        "workflowResults": {"checkout": "WORKFLOW_FAILED"},
    }


def test_complete_manifest_becomes_metric_ready_evidence() -> None:
    manifest = parse_captured_experiment_manifest(_payload())
    evidence = require_complete_manifest_evidence(manifest)
    assessment = assess_validation_evidence(evidence)

    assert manifest.complete is True
    assert evidence.experiment_id == "worker-1-failure"
    assert evidence.failed_node == "resilience-research-worker"
    assert evidence.failed_services == ("paymentservice",)
    assert evidence.failed_workflows == ("checkout",)
    assert evidence.observed_at == datetime(
        2026,
        10,
        21,
        5,
        0,
        tzinfo=timezone.utc,
    )
    assert assessment.ready_for_validation is True


def test_missing_probe_result_remains_incomplete_instead_of_successful() -> None:
    payload = _payload()
    payload["workflowResults"] = {}

    manifest = parse_captured_experiment_manifest(payload)

    assert manifest.complete is False
    assert manifest.workflow_capture.missing_workflows == ("checkout",)
    assert manifest.workflow_capture.evidence.failed_workflows is None
    with pytest.raises(IncompleteCapturedExperimentError):
        require_complete_manifest_evidence(manifest)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.pop("failedNode"), "missing fields"),
        (
            lambda payload: payload.update({"unexpected": "value"}),
            "unknown fields",
        ),
        (
            lambda payload: payload.update(
                {"observedAt": "2026-10-21T10:30:00"}
            ),
            "timezone",
        ),
        (
            lambda payload: payload.update(
                {"expectedServices": "paymentservice"}
            ),
            "array of strings",
        ),
        (
            lambda payload: payload.update({"serviceResults": []}),
            "object of string results",
        ),
        (
            lambda payload: payload.update(
                {"workflowResults": {"checkout": "UNKNOWN"}}
            ),
            "WORKFLOW_SUCCEEDED",
        ),
    ],
)
def test_invalid_manifest_structure_is_rejected(
    mutation,
    message: str,
) -> None:
    payload = _payload()
    mutation(payload)

    with pytest.raises(ValueError, match=message):
        parse_captured_experiment_manifest(payload)


def test_manifest_parser_does_not_modify_raw_payload() -> None:
    payload = _payload()
    before = deepcopy(payload)

    parse_captured_experiment_manifest(payload)

    assert payload == before
