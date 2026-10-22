import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.engine.failure_impact_prediction.baseline_comparison import (
    compare_failure_impact_methods,
)
from app.engine.failure_impact_prediction.baseline_export import (
    ARTIFACT_TYPE,
    build_method_comparison_export,
    write_method_comparison_artifact,
)
from app.engine.failure_impact_prediction.fixture_provider import (
    get_demo_inputs,
)


def _comparison():
    workflow, deployment = get_demo_inputs()
    return compare_failure_impact_methods(
        workflow,
        deployment,
        "node-1",
        dependency_scenario_services={"cartservice"},
    )


def _artifact() -> dict[str, object]:
    return build_method_comparison_export(
        _comparison(),
        generated_at=datetime(
            2026,
            10,
            22,
            9,
            30,
            tzinfo=timezone(timedelta(hours=5, minutes=30)),
        ),
    )


def test_export_is_json_serializable_and_traceable() -> None:
    artifact = _artifact()

    assert json.loads(json.dumps(artifact)) == artifact
    assert artifact["artifactType"] == ARTIFACT_TYPE
    assert artifact["methodologyVersion"] == "1.0"
    assert artifact["generatedAt"] == "2026-10-22T04:00:00Z"
    assert artifact["workflowId"] == "checkout"
    assert artifact["deploymentSnapshotId"] == "component3-demo-snapshot-v1"
    assert artifact["failedNode"] == "node-1"
    assert artifact["dependencyScenarioServices"] == ["cartservice"]


def test_export_keeps_method_outputs_and_scopes_separate() -> None:
    artifact = _artifact()
    methods = artifact["methods"]

    assert isinstance(methods, dict)
    assert methods["replicaCountOnly"]["vulnerableServices"] == [
        "emailservice"
    ]
    assert methods["deploymentOnly"]["nodeInducedFailures"] == [
        "cartservice"
    ]
    assert methods["dependencyOnly"]["failedMandatoryServices"] == [
        "cartservice"
    ]
    assert methods["proposedCrossLayer"]["failedServices"] == [
        "cartservice"
    ]
    assert methods["proposedCrossLayer"]["failedWorkflows"] == ["checkout"]
    assert "evidenceScope" in methods["replicaCountOnly"]
    assert "evidenceScope" in methods["deploymentOnly"]
    assert "evidenceScope" in methods["dependencyOnly"]
    assert "evidenceScope" in methods["proposedCrossLayer"]


def test_export_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone information"):
        build_method_comparison_export(
            _comparison(),
            generated_at=datetime(2026, 10, 22, 9, 30),
        )


def test_export_rejects_mismatched_comparison_identity() -> None:
    comparison = _comparison()
    mismatched = replace(
        comparison,
        deployment_snapshot_id="different-snapshot",
    )

    with pytest.raises(ValueError, match="same deployment snapshot"):
        build_method_comparison_export(
            mismatched,
            generated_at=datetime.now(timezone.utc),
        )


def test_writer_creates_parent_and_preserves_json(tmp_path) -> None:
    output = tmp_path / "run-01" / "method-comparison.json"
    artifact = _artifact()

    result = write_method_comparison_artifact(output, artifact)

    assert result == output
    assert json.loads(output.read_text(encoding="utf-8")) == artifact
    assert output.read_bytes().endswith(b"\n")


def test_writer_refuses_to_overwrite_existing_artifact(tmp_path) -> None:
    output = tmp_path / "method-comparison.json"
    output.write_text("preserve me", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        write_method_comparison_artifact(output, _artifact())

    assert output.read_text(encoding="utf-8") == "preserve me"


def test_writer_rejects_unrelated_artifact(tmp_path) -> None:
    output = tmp_path / "method-comparison.json"

    with pytest.raises(ValueError, match="artifactType"):
        write_method_comparison_artifact(output, {"artifactType": "OTHER"})

    assert not output.exists()
