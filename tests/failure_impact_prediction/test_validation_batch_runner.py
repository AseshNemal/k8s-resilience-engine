import json

import pytest

from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.service import predict_failure_impact
from experiments.failure_impact_validation.run_validation import (
    _batch_manifest_inputs,
    evaluate_saved_experiments,
    main,
)


def _prediction_payload() -> dict[str, object]:
    workflow, deployment = get_demo_inputs()
    return predict_failure_impact(
        workflow,
        deployment,
        "node-1",
    ).model_dump(by_alias=True, mode="json")


def _evidence_payload(
    experiment_id: str,
    *,
    service_result: str = "SERVICE_UNAVAILABLE",
    workflow_result: str = "WORKFLOW_FAILED",
) -> dict[str, object]:
    return {
        "experimentId": experiment_id,
        "failedNode": "node-1",
        "observationSource": "connected-cluster probes",
        "observedAt": "2026-10-22T01:00:00Z",
        "namespace": "research",
        "expectedServices": ["cartservice"],
        "serviceResults": {"cartservice": service_result},
        "expectedWorkflows": ["checkout"],
        "workflowResults": {"checkout": workflow_result},
    }


def test_batch_runner_aggregates_repeated_experiment_metrics() -> None:
    artifact = evaluate_saved_experiments(
        batch_id="worker-1-repeated-validation",
        experiments=[
            (_prediction_payload(), _evidence_payload("run-1")),
            (
                _prediction_payload(),
                _evidence_payload(
                    "run-2",
                    service_result="SERVICE_AVAILABLE",
                    workflow_result="WORKFLOW_SUCCEEDED",
                ),
            ),
        ],
    )

    validation = artifact["validation"]
    assert validation["totalExperiments"] == 2
    assert validation["exactMatchCount"] == 1
    assert validation["exactMatchRate"] == pytest.approx(0.5)
    assert validation["serviceMetrics"]["precision"] == pytest.approx(0.5)
    assert validation["serviceMetrics"]["recall"] == pytest.approx(1.0)
    assert validation["workflowMetrics"]["precision"] == pytest.approx(0.5)
    assert validation["workflowMetrics"]["recall"] == pytest.approx(1.0)


def test_batch_manifest_resolves_paths_relative_to_itself(
    tmp_path,
    capsys,
) -> None:
    captures = tmp_path / "captures"
    captures.mkdir()
    entries = []
    for run_number in (1, 2):
        prediction_path = captures / f"prediction-{run_number}.json"
        evidence_path = captures / f"evidence-{run_number}.json"
        prediction_path.write_text(
            json.dumps(_prediction_payload()),
            encoding="utf-8",
        )
        evidence_path.write_text(
            json.dumps(_evidence_payload(f"run-{run_number}")),
            encoding="utf-8",
        )
        entries.append(
            {
                "prediction": f"captures/{prediction_path.name}",
                "evidence": f"captures/{evidence_path.name}",
            }
        )
    manifest_path = tmp_path / "batch.json"
    manifest_path.write_text(
        json.dumps(
            {
                "batchId": "relative-path-batch",
                "experiments": entries,
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["--batch-manifest", str(manifest_path)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["validation"]["batchId"] == "relative-path-batch"
    assert output["validation"]["totalExperiments"] == 2


def test_batch_manifest_rejects_empty_experiment_list(tmp_path) -> None:
    manifest_path = tmp_path / "batch.json"
    manifest_path.write_text(
        json.dumps({"batchId": "empty", "experiments": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="non-empty array"):
        _batch_manifest_inputs(manifest_path)


def test_cli_rejects_mixed_single_and_batch_modes(tmp_path) -> None:
    manifest_path = tmp_path / "batch.json"

    with pytest.raises(SystemExit) as error:
        main(
            [
                "--batch-manifest",
                str(manifest_path),
                "--prediction",
                "prediction.json",
            ]
        )

    assert error.value.code == 2
