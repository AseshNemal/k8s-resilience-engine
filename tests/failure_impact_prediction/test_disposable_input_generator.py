import json
from datetime import datetime, timezone

import pytest

from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.workflow import WorkflowGraph
from experiments.failure_impact_validation.generate_disposable_inputs import (
    DISCLAIMER,
    EVIDENCE_STATUS,
    build_disposable_inputs,
    cli,
    main,
    write_disposable_inputs,
)


GENERATED_AT = datetime(2026, 10, 23, 4, 30, tzinfo=timezone.utc)


def test_generator_builds_complete_schema_valid_inputs() -> None:
    workflow, deployment, metadata = build_disposable_inputs(
        "viva-01",
        generated_at=GENERATED_AT,
    )

    assert WorkflowGraph.model_validate(workflow.model_dump()) == workflow
    assert DeploymentGraph.model_validate(deployment.model_dump()) == deployment
    assert workflow.workflow_id == "synthetic-viva-01-checkout"
    assert deployment.snapshot_id == "synthetic-viva-01-deployment"
    assert len(deployment.nodes) == 3
    assert {service.name for service in deployment.services} == {
        "frontend",
        "checkoutservice",
        "cartservice",
        "paymentservice",
        "emailservice",
    }
    assert all(
        pod.owner_name == service.name
        for service in deployment.services
        for pod in service.pods
    )
    assert next(
        service.exposed_failure_domains
        for service in deployment.services
        if service.name == "cartservice"
    ) == ["synthetic-node-1"]
    assert metadata["evidenceStatus"] == EVIDENCE_STATUS
    assert metadata["disclaimer"] == DISCLAIMER


def test_recommended_node_demonstrates_mandatory_service_failure() -> None:
    workflow, deployment, metadata = build_disposable_inputs(
        "viva-01",
        generated_at=GENERATED_AT,
    )

    report = predict_failure_impact(
        workflow,
        deployment,
        str(metadata["recommendedFailedNode"]),
    )

    assert report.failed_services == ["cartservice"]
    assert report.failed_workflows == [workflow.workflow_id]
    assert report.blast_radius == pytest.approx(100.0)


def test_optional_service_failure_keeps_workflow_alive() -> None:
    workflow, deployment, _ = build_disposable_inputs(
        "viva-01",
        generated_at=GENERATED_AT,
    )

    report = predict_failure_impact(
        workflow,
        deployment,
        "synthetic-node-3",
    )

    assert report.failed_services == ["emailservice"]
    assert report.failed_workflows == []
    assert report.blast_radius == pytest.approx(0.0)


def test_writer_creates_labelled_artifacts_without_overwriting(tmp_path) -> None:
    output_dir = tmp_path / "viva-inputs"

    metadata = write_disposable_inputs(
        output_dir,
        "viva-01",
        generated_at=GENERATED_AT,
    )

    workflow = json.loads(
        (output_dir / "workflow-graph.json").read_text(encoding="utf-8")
    )
    deployment = json.loads(
        (output_dir / "deployment-graph.json").read_text(encoding="utf-8")
    )
    saved_metadata = json.loads(
        (output_dir / "synthetic-input-metadata.json").read_text(
            encoding="utf-8"
        )
    )
    assert workflow["workflowId"] == "synthetic-viva-01-checkout"
    assert deployment["snapshotId"] == "synthetic-viva-01-deployment"
    assert saved_metadata == metadata
    assert saved_metadata["evidenceStatus"] == "SYNTHETIC_DISPOSABLE"

    with pytest.raises(ValueError, match="refusing to overwrite"):
        write_disposable_inputs(
            output_dir,
            "viva-01",
            generated_at=GENERATED_AT,
        )


def test_generator_rejects_unsafe_scenario_identifier() -> None:
    with pytest.raises(ValueError, match="scenario_id"):
        build_disposable_inputs(
            "../escape",
            generated_at=GENERATED_AT,
        )


def test_cli_writes_files_and_prints_metadata(tmp_path, capsys) -> None:
    output_dir = tmp_path / "cli-inputs"

    exit_code = main(
        [
            "--scenario-id",
            "viva-cli",
            "--output-dir",
            str(output_dir),
        ]
    )
    printed = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert printed["evidenceStatus"] == EVIDENCE_STATUS
    assert (output_dir / "workflow-graph.json").is_file()
    assert (output_dir / "deployment-graph.json").is_file()


def test_cli_reports_existing_output_without_traceback(tmp_path, capsys) -> None:
    output_dir = tmp_path / "existing"
    write_disposable_inputs(
        output_dir,
        "viva-existing",
        generated_at=GENERATED_AT,
    )

    exit_code = cli(
        [
            "--scenario-id",
            "viva-existing",
            "--output-dir",
            str(output_dir),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "error: refusing to overwrite" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
