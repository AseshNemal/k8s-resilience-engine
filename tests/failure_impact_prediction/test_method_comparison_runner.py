import json
from datetime import datetime, timezone

import pytest

from app.engine.failure_impact_prediction.baseline_export import ARTIFACT_TYPE
from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from experiments.failure_impact_validation.run_method_comparison import (
    cli,
    evaluate_saved_method_comparison,
    main,
)


def _payloads() -> tuple[dict[str, object], dict[str, object]]:
    workflow, deployment = get_demo_inputs()
    return (
        workflow.model_dump(by_alias=True, mode="json"),
        deployment.model_dump(by_alias=True, mode="json"),
    )


def test_runner_validates_inputs_and_builds_comparison() -> None:
    workflow, deployment = _payloads()

    artifact = evaluate_saved_method_comparison(
        workflow_payload=workflow,
        deployment_payload=deployment,
        failed_node="node-1",
        dependency_scenario_services=["cartservice"],
        generated_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
    )

    assert artifact["artifactType"] == ARTIFACT_TYPE
    assert artifact["workflowId"] == "checkout"
    assert artifact["failedNode"] == "node-1"
    methods = artifact["methods"]
    assert isinstance(methods, dict)
    assert methods["deploymentOnly"]["nodeInducedFailures"] == [
        "cartservice"
    ]
    assert methods["proposedCrossLayer"]["failedWorkflows"] == ["checkout"]


def test_runner_normalizes_declared_dependency_scenario() -> None:
    workflow, deployment = _payloads()

    artifact = evaluate_saved_method_comparison(
        workflow_payload=workflow,
        deployment_payload=deployment,
        failed_node=" node-1 ",
        dependency_scenario_services=[
            " paymentservice ",
            "cartservice",
            "paymentservice",
        ],
        generated_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
    )

    assert artifact["failedNode"] == "node-1"
    assert artifact["dependencyScenarioServices"] == [
        "cartservice",
        "paymentservice",
    ]


def test_runner_requires_explicit_dependency_scenario() -> None:
    workflow, deployment = _payloads()

    with pytest.raises(ValueError, match="at least one"):
        evaluate_saved_method_comparison(
            workflow_payload=workflow,
            deployment_payload=deployment,
            failed_node="node-1",
            dependency_scenario_services=[],
            generated_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
        )


def test_runner_rejects_invalid_upstream_schema() -> None:
    workflow, deployment = _payloads()
    workflow.pop("workflowId")

    with pytest.raises(ValueError, match="workflowId"):
        evaluate_saved_method_comparison(
            workflow_payload=workflow,
            deployment_payload=deployment,
            failed_node="node-1",
            dependency_scenario_services=["cartservice"],
            generated_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
        )


def test_cli_reads_saved_inputs_writes_and_prints_artifact(
    tmp_path,
    capsys,
) -> None:
    workflow, deployment = _payloads()
    workflow_path = tmp_path / "workflow.json"
    deployment_path = tmp_path / "deployment.json"
    output_path = tmp_path / "results" / "method-comparison.json"
    workflow_path.write_text(json.dumps(workflow), encoding="utf-8")
    deployment_path.write_text(json.dumps(deployment), encoding="utf-8")

    exit_code = main(
        [
            "--workflow",
            str(workflow_path),
            "--deployment",
            str(deployment_path),
            "--failed-node",
            "node-1",
            "--dependency-scenario-service",
            "cartservice",
            "--output",
            str(output_path),
        ]
    )
    stdout_artifact = json.loads(capsys.readouterr().out)
    saved_artifact = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert saved_artifact == stdout_artifact
    assert saved_artifact["artifactType"] == ARTIFACT_TYPE


def test_cli_refuses_to_overwrite_comparison(tmp_path) -> None:
    workflow, deployment = _payloads()
    workflow_path = tmp_path / "workflow.json"
    deployment_path = tmp_path / "deployment.json"
    output_path = tmp_path / "method-comparison.json"
    workflow_path.write_text(json.dumps(workflow), encoding="utf-8")
    deployment_path.write_text(json.dumps(deployment), encoding="utf-8")
    output_path.write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        main(
            [
                "--workflow",
                str(workflow_path),
                "--deployment",
                str(deployment_path),
                "--failed-node",
                "node-1",
                "--dependency-scenario-service",
                "cartservice",
                "--output",
                str(output_path),
            ]
        )

    assert output_path.read_text(encoding="utf-8") == "preserve"


def test_command_reports_missing_input_without_traceback(tmp_path, capsys) -> None:
    exit_code = cli(
        [
            "--workflow",
            str(tmp_path / "missing-workflow.json"),
            "--deployment",
            str(tmp_path / "missing-deployment.json"),
            "--failed-node",
            "node-1",
            "--dependency-scenario-service",
            "cartservice",
            "--output",
            str(tmp_path / "comparison.json"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "error: Could not read" in captured.err
    assert "missing-workflow.json" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
