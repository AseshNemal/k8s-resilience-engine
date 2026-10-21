import json

import pytest

from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.failure_impact_prediction.captured_evidence_combiner import (
    IncompleteCapturedExperimentError,
)
from experiments.failure_impact_validation.run_validation import (
    ARTIFACT_TYPE,
    EVIDENCE_STATUS,
    cli,
    evaluate_saved_experiment,
    main,
)


def _prediction_payload(*, failed_node: str = "node-1") -> dict[str, object]:
    workflow, deployment = get_demo_inputs()
    prediction = predict_failure_impact(workflow, deployment, failed_node)
    return prediction.model_dump(by_alias=True, mode="json")


def _evidence_payload(*, failed_node: str = "node-1") -> dict[str, object]:
    return {
        "experimentId": "captured-node-1-failure",
        "failedNode": failed_node,
        "observationSource": "connected-cluster experiment manifest",
        "observedAt": "2026-10-21T05:00:00Z",
        "workflowFreezeId": "checkout-freeze-v1",
        "deploymentSnapshotId": "deployment-before-node-1",
        "namespace": "research",
        "expectedServices": ["cartservice"],
        "serviceResults": {"cartservice": "SERVICE_UNAVAILABLE"},
        "expectedWorkflows": ["checkout"],
        "workflowResults": {"checkout": "WORKFLOW_FAILED"},
    }


def test_runner_evaluates_saved_prediction_and_complete_evidence() -> None:
    artifact = evaluate_saved_experiment(
        batch_id="captured-validation",
        prediction_payload=_prediction_payload(),
        evidence_payload=_evidence_payload(),
    )

    assert artifact["artifactType"] == ARTIFACT_TYPE
    assert artifact["evidenceStatus"] == EVIDENCE_STATUS
    validation = artifact["validation"]
    assert isinstance(validation, dict)
    assert validation["batchId"] == "captured-validation"
    assert validation["totalExperiments"] == 1
    assert validation["exactMatchCount"] == 1
    assert validation["exactMatchRate"] == pytest.approx(1.0)


def test_runner_blocks_incomplete_evidence() -> None:
    evidence = _evidence_payload()
    evidence["workflowResults"] = {}

    with pytest.raises(IncompleteCapturedExperimentError):
        evaluate_saved_experiment(
            batch_id="incomplete-validation",
            prediction_payload=_prediction_payload(),
            evidence_payload=evidence,
        )


def test_runner_rejects_prediction_evidence_node_mismatch() -> None:
    with pytest.raises(ValueError, match="same failed node"):
        evaluate_saved_experiment(
            batch_id="wrong-node-validation",
            prediction_payload=_prediction_payload(),
            evidence_payload=_evidence_payload(failed_node="node-2"),
        )


def test_cli_reads_artifacts_and_prints_json(
    tmp_path,
    capsys,
) -> None:
    prediction_path = tmp_path / "prediction.json"
    evidence_path = tmp_path / "evidence.json"
    prediction_path.write_text(
        json.dumps(_prediction_payload()),
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps(_evidence_payload()),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--prediction",
            str(prediction_path),
            "--evidence",
            str(evidence_path),
            "--batch-id",
            "cli-validation",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["artifactType"] == ARTIFACT_TYPE
    assert output["evidenceStatus"] == EVIDENCE_STATUS
    assert output["validation"]["batchId"] == "cli-validation"


def test_cli_rejects_non_object_json(tmp_path) -> None:
    prediction_path = tmp_path / "prediction.json"
    evidence_path = tmp_path / "evidence.json"
    prediction_path.write_text("[]", encoding="utf-8")
    evidence_path.write_text(
        json.dumps(_evidence_payload()),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must contain a JSON object"):
        main(
            [
                "--prediction",
                str(prediction_path),
                "--evidence",
                str(evidence_path),
                "--batch-id",
                "invalid-cli-validation",
            ]
        )


def test_cli_writes_artifact_and_preserves_stdout(tmp_path, capsys) -> None:
    prediction_path = tmp_path / "prediction.json"
    evidence_path = tmp_path / "evidence.json"
    output_path = tmp_path / "results" / "validation.json"
    prediction_path.write_text(
        json.dumps(_prediction_payload()),
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps(_evidence_payload()),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--prediction",
            str(prediction_path),
            "--evidence",
            str(evidence_path),
            "--batch-id",
            "saved-cli-validation",
            "--output",
            str(output_path),
        ]
    )
    stdout_artifact = json.loads(capsys.readouterr().out)
    saved_artifact = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert saved_artifact == stdout_artifact
    assert saved_artifact["validation"]["batchId"] == (
        "saved-cli-validation"
    )


def test_cli_refuses_to_overwrite_validation_artifact(
    tmp_path,
) -> None:
    prediction_path = tmp_path / "prediction.json"
    evidence_path = tmp_path / "evidence.json"
    output_path = tmp_path / "validation.json"
    prediction_path.write_text(
        json.dumps(_prediction_payload()),
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps(_evidence_payload()),
        encoding="utf-8",
    )
    output_path.write_text("keep-existing-result", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        main(
            [
                "--prediction",
                str(prediction_path),
                "--evidence",
                str(evidence_path),
                "--batch-id",
                "protected-cli-validation",
                "--output",
                str(output_path),
            ]
        )

    assert output_path.read_text(encoding="utf-8") == (
        "keep-existing-result"
    )


def test_command_reports_missing_file_without_traceback(
    tmp_path,
    capsys,
) -> None:
    missing_prediction = tmp_path / "missing-prediction.json"
    missing_evidence = tmp_path / "missing-evidence.json"

    exit_code = cli(
        [
            "--prediction",
            str(missing_prediction),
            "--evidence",
            str(missing_evidence),
            "--batch-id",
            "missing-input-validation",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "error: Could not read" in captured.err
    assert str(missing_prediction) in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
