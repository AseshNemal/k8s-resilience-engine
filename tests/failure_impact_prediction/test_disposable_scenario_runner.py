import json
from datetime import datetime, timezone

import pytest

from experiments.failure_impact_validation.generate_disposable_inputs import (
    build_disposable_inputs,
    write_disposable_inputs,
)
from experiments.failure_impact_validation.run_disposable_scenarios import (
    ARTIFACT_TYPE,
    cli,
    evaluate_disposable_scenarios,
    format_compact_summary,
    main,
    run_disposable_scenarios,
)


GENERATED_AT = datetime(2026, 10, 23, 11, 15, tzinfo=timezone.utc)


def _input_payloads():
    workflow, deployment, metadata = build_disposable_inputs(
        "viva-01",
        generated_at=GENERATED_AT,
    )
    return (
        metadata,
        workflow.model_dump(by_alias=True, mode="json"),
        deployment.model_dump(by_alias=True, mode="json"),
    )


def test_runner_evaluates_every_node_when_none_is_selected() -> None:
    metadata, workflow, deployment = _input_payloads()

    artifact = evaluate_disposable_scenarios(
        metadata=metadata,
        workflow_payload=workflow,
        deployment_payload=deployment,
        failed_nodes=[],
        generated_at=GENERATED_AT,
    )

    assert artifact["artifactType"] == ARTIFACT_TYPE
    assert artifact["evidenceStatus"] == "SYNTHETIC_DISPOSABLE"
    assert artifact["evaluatedNodes"] == [
        "synthetic-node-1",
        "synthetic-node-2",
        "synthetic-node-3",
    ]
    assert artifact["summary"] == {
        "totalNodesEvaluated": 3,
        "highestBlastRadius": 100.0,
        "highestRiskNodes": ["synthetic-node-1"],
        "workflowFailureNodes": ["synthetic-node-1"],
        "serviceOnlyFailureNodes": ["synthetic-node-3"],
        "noServiceFailureNodes": ["synthetic-node-2"],
    }
    reports = {
        report["failedNode"]: report
        for report in artifact["results"]
    }
    assert reports["synthetic-node-1"]["failedServices"] == ["cartservice"]
    assert reports["synthetic-node-1"]["failedWorkflows"] == [
        "synthetic-viva-01-checkout"
    ]
    assert reports["synthetic-node-1"]["blastRadius"] == pytest.approx(100.0)
    assert reports["synthetic-node-2"]["failedServices"] == []
    assert reports["synthetic-node-2"]["failedWorkflows"] == []
    assert reports["synthetic-node-3"]["failedServices"] == ["emailservice"]
    assert reports["synthetic-node-3"]["failedWorkflows"] == []


def test_runner_can_evaluate_one_selected_node() -> None:
    metadata, workflow, deployment = _input_payloads()

    artifact = evaluate_disposable_scenarios(
        metadata=metadata,
        workflow_payload=workflow,
        deployment_payload=deployment,
        failed_nodes=["synthetic-node-3"],
        generated_at=GENERATED_AT,
    )

    assert artifact["evaluatedNodes"] == ["synthetic-node-3"]
    assert artifact["summary"] == {
        "totalNodesEvaluated": 1,
        "highestBlastRadius": 0.0,
        "highestRiskNodes": ["synthetic-node-3"],
        "workflowFailureNodes": [],
        "serviceOnlyFailureNodes": ["synthetic-node-3"],
        "noServiceFailureNodes": [],
    }
    assert len(artifact["results"]) == 1
    assert artifact["results"][0]["failedNode"] == "synthetic-node-3"


def test_compact_summary_explains_each_node_outcome() -> None:
    metadata, workflow, deployment = _input_payloads()
    artifact = evaluate_disposable_scenarios(
        metadata=metadata,
        workflow_payload=workflow,
        deployment_payload=deployment,
        failed_nodes=[],
        generated_at=GENERATED_AT,
    )

    summary = format_compact_summary(artifact)

    assert "Evidence status: SYNTHETIC_DISPOSABLE" in summary
    assert "not observed Kubernetes evidence" in summary
    assert (
        "synthetic-node-1 | 100.0% | 0.0% | cartservice | FAILED: "
        "synthetic-viva-01-checkout"
    ) in summary
    assert "synthetic-node-2 | 0.0% | 100.0% | none | SURVIVES" in summary
    assert (
        "synthetic-node-3 | 0.0% | 100.0% | emailservice | SURVIVES"
    ) in summary
    assert "Highest-risk nodes: synthetic-node-1" in summary


def test_runner_rejects_unknown_node() -> None:
    metadata, workflow, deployment = _input_payloads()

    with pytest.raises(ValueError, match="unknown failed nodes: missing-node"):
        evaluate_disposable_scenarios(
            metadata=metadata,
            workflow_payload=workflow,
            deployment_payload=deployment,
            failed_nodes=["missing-node"],
            generated_at=GENERATED_AT,
        )


def test_runner_rejects_blank_selected_node() -> None:
    metadata, workflow, deployment = _input_payloads()

    with pytest.raises(ValueError, match="failed nodes must be non-empty"):
        evaluate_disposable_scenarios(
            metadata=metadata,
            workflow_payload=workflow,
            deployment_payload=deployment,
            failed_nodes=["  "],
            generated_at=GENERATED_AT,
        )


def test_runner_rejects_inputs_without_disposable_label() -> None:
    metadata, workflow, deployment = _input_payloads()
    metadata["evidenceStatus"] = "CAPTURED_EXPERIMENT"

    with pytest.raises(ValueError, match="SYNTHETIC_DISPOSABLE"):
        evaluate_disposable_scenarios(
            metadata=metadata,
            workflow_payload=workflow,
            deployment_payload=deployment,
            failed_nodes=[],
            generated_at=GENERATED_AT,
        )


def test_runner_rejects_graph_from_another_disposable_scenario() -> None:
    metadata, workflow, deployment = _input_payloads()
    workflow["workflowId"] = "synthetic-another-checkout"

    with pytest.raises(ValueError, match="does not match"):
        evaluate_disposable_scenarios(
            metadata=metadata,
            workflow_payload=workflow,
            deployment_payload=deployment,
            failed_nodes=[],
            generated_at=GENERATED_AT,
        )


def test_file_runner_writes_result_without_overwriting(tmp_path) -> None:
    input_dir = tmp_path / "inputs"
    output = tmp_path / "results" / "all-nodes.json"
    write_disposable_inputs(
        input_dir,
        "file-run",
        generated_at=GENERATED_AT,
    )

    artifact = run_disposable_scenarios(
        input_dir=input_dir,
        failed_nodes=[],
        output=output,
        generated_at=GENERATED_AT,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == artifact
    with pytest.raises(ValueError, match="refusing to overwrite"):
        run_disposable_scenarios(
            input_dir=input_dir,
            failed_nodes=[],
            output=output,
            generated_at=GENERATED_AT,
        )


def test_cli_reports_missing_input_without_traceback(tmp_path, capsys) -> None:
    exit_code = cli(
        [
            "--input-dir",
            str(tmp_path / "missing"),
            "--output",
            str(tmp_path / "result.json"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "error: Could not read" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_cli_compact_mode_prints_table_and_saves_full_json(
    tmp_path,
    capsys,
) -> None:
    input_dir = tmp_path / "inputs"
    output = tmp_path / "compact-result.json"
    write_disposable_inputs(
        input_dir,
        "compact",
        generated_at=GENERATED_AT,
    )

    exit_code = main(
        [
            "--input-dir",
            str(input_dir),
            "--output",
            str(output),
            "--compact",
        ]
    )
    printed = capsys.readouterr().out
    saved = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert "Node | Blast radius | Survivability" in printed
    assert not printed.lstrip().startswith("{")
    assert saved["artifactType"] == ARTIFACT_TYPE
    assert len(saved["results"]) == 3
