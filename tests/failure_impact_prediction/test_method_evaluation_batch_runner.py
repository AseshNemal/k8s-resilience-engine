import json
from datetime import datetime, timezone

import pytest

from app.engine.failure_impact_prediction.baseline_comparison import (
    compare_failure_impact_methods,
)
from app.engine.failure_impact_prediction.baseline_export import (
    build_method_comparison_export,
)
from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.method_evaluation_batch_export import (
    ARTIFACT_TYPE,
)
from experiments.failure_impact_validation.run_method_evaluation_batch import (
    _batch_manifest_inputs,
    cli,
    evaluate_saved_method_batch,
    main,
)


COMPARISON_TIME = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)
OBSERVATION_TIME = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
BATCH_TIME = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
SERVICES = (
    "frontend",
    "paymentservice",
    "cartservice",
    "checkoutservice",
    "emailservice",
)


def _comparison_payload(failed_node: str) -> dict[str, object]:
    workflow, deployment = get_demo_inputs()
    comparison = compare_failure_impact_methods(
        workflow,
        deployment,
        failed_node,
        dependency_scenario_services={"cartservice"},
    )
    return build_method_comparison_export(
        comparison,
        generated_at=COMPARISON_TIME,
    )


def _evidence_payload(
    *,
    experiment_id: str,
    failed_node: str,
    failed_services: tuple[str, ...],
    workflow_failed: bool,
) -> dict[str, object]:
    return {
        "experimentId": experiment_id,
        "failedNode": failed_node,
        "observationSource": "controlled cluster probes",
        "observedAt": OBSERVATION_TIME.isoformat(),
        "workflowFreezeId": "checkout-freeze-v1",
        "deploymentSnapshotId": "component3-demo-snapshot-v1",
        "namespace": "research",
        "expectedServices": list(SERVICES),
        "serviceResults": {
            service: (
                "SERVICE_UNAVAILABLE"
                if service in failed_services
                else "SERVICE_AVAILABLE"
            )
            for service in SERVICES
        },
        "expectedWorkflows": ["checkout"],
        "workflowResults": {
            "checkout": (
                "WORKFLOW_FAILED"
                if workflow_failed
                else "WORKFLOW_SUCCEEDED"
            )
        },
    }


def _experiments():
    return (
        (
            _comparison_payload("node-1"),
            _evidence_payload(
                experiment_id="node-1-run",
                failed_node="node-1",
                failed_services=("cartservice",),
                workflow_failed=True,
            ),
        ),
        (
            _comparison_payload("node-2"),
            _evidence_payload(
                experiment_id="node-2-run",
                failed_node="node-2",
                failed_services=(),
                workflow_failed=False,
            ),
        ),
    )


def test_runner_revalidates_and_aggregates_repeated_experiments() -> None:
    artifact = evaluate_saved_method_batch(
        batch_id="repeated-node-failures",
        experiments=_experiments(),
        generated_at=BATCH_TIME,
    )

    assert artifact["artifactType"] == ARTIFACT_TYPE
    assert artifact["batchId"] == "repeated-node-failures"
    assert artifact["totalExperiments"] == 2
    assert artifact["experimentIds"] == ["node-1-run", "node-2-run"]
    methods = artifact["methods"]
    assert isinstance(methods, dict)
    assert methods["replicaCountOnly"]["workflowMetrics"] is None
    assert methods["deploymentOnly"]["serviceMetrics"]["exactMatchRate"] == 1.0
    assert methods["dependencyOnly"]["workflowMetrics"]["precision"] == 0.5
    assert methods["proposedCrossLayer"]["serviceMetrics"]["exactMatchRate"] == 1.0
    assert methods["proposedCrossLayer"]["workflowMetrics"]["exactMatchRate"] == 1.0


def test_runner_rejects_report_time_before_observation() -> None:
    with pytest.raises(ValueError, match="earlier than an experiment"):
        evaluate_saved_method_batch(
            batch_id="invalid-time",
            experiments=_experiments(),
            generated_at=datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc),
        )


def test_manifest_resolves_paths_relative_to_itself(tmp_path) -> None:
    manifest_path = tmp_path / "batch.json"
    manifest_path.write_text(
        json.dumps(
            {
                "batchId": "relative-paths",
                "experiments": [
                    {
                        "comparison": "runs/run-1/comparison.json",
                        "evidence": "runs/run-1/evidence.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    batch_id, pairs = _batch_manifest_inputs(manifest_path)

    assert batch_id == "relative-paths"
    assert pairs == (
        (
            tmp_path / "runs" / "run-1" / "comparison.json",
            tmp_path / "runs" / "run-1" / "evidence.json",
        ),
    )


def test_manifest_rejects_unknown_fields(tmp_path) -> None:
    manifest_path = tmp_path / "batch.json"
    manifest_path.write_text(
        json.dumps(
            {
                "batchId": "invalid",
                "experiments": [],
                "unexpected": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown fields"):
        _batch_manifest_inputs(manifest_path)


def test_cli_reads_manifest_writes_and_prints_batch(tmp_path, capsys) -> None:
    entries: list[dict[str, str]] = []
    for index, (comparison, evidence) in enumerate(_experiments(), start=1):
        run_directory = tmp_path / "runs" / f"run-{index}"
        run_directory.mkdir(parents=True)
        comparison_path = run_directory / "method-comparison.json"
        evidence_path = run_directory / "evidence.json"
        comparison_path.write_text(json.dumps(comparison), encoding="utf-8")
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        entries.append(
            {
                "comparison": str(comparison_path.relative_to(tmp_path)),
                "evidence": str(evidence_path.relative_to(tmp_path)),
            }
        )

    manifest_path = tmp_path / "batch.json"
    output_path = tmp_path / "method-batch.json"
    manifest_path.write_text(
        json.dumps({"batchId": "cli-batch", "experiments": entries}),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--batch-manifest",
            str(manifest_path),
            "--output",
            str(output_path),
        ]
    )
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert saved == printed
    assert saved["totalExperiments"] == 2


def test_cli_reports_missing_manifest_without_traceback(tmp_path, capsys) -> None:
    exit_code = cli(
        [
            "--batch-manifest",
            str(tmp_path / "missing-batch.json"),
            "--output",
            str(tmp_path / "batch-output.json"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "error: Could not read" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
