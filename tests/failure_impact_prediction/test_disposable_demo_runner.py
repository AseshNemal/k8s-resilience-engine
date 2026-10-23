import json
from datetime import datetime, timezone

import pytest

from experiments.failure_impact_validation.run_disposable_demo import (
    RESULT_FILE,
    cli,
    main,
    run_disposable_demo,
)


GENERATED_AT = datetime(2026, 10, 23, 12, 45, tzinfo=timezone.utc)


def test_demo_runner_creates_complete_all_node_bundle(tmp_path) -> None:
    output_dir = tmp_path / "viva-demo"

    artifact = run_disposable_demo(
        scenario_id="one-command",
        output_dir=output_dir,
        failed_nodes=[],
        generated_at=GENERATED_AT,
    )

    assert sorted(path.name for path in output_dir.iterdir()) == [
        "deployment-graph.json",
        RESULT_FILE,
        "synthetic-input-metadata.json",
        "workflow-graph.json",
    ]
    assert artifact["scenarioId"] == "one-command"
    assert artifact["evidenceStatus"] == "SYNTHETIC_DISPOSABLE"
    assert artifact["evaluatedNodes"] == [
        "synthetic-node-1",
        "synthetic-node-2",
        "synthetic-node-3",
    ]
    assert json.loads(
        (output_dir / RESULT_FILE).read_text(encoding="utf-8")
    ) == artifact


def test_demo_runner_can_select_one_node(tmp_path) -> None:
    artifact = run_disposable_demo(
        scenario_id="single-node",
        output_dir=tmp_path / "single-node",
        failed_nodes=["synthetic-node-3"],
        generated_at=GENERATED_AT,
    )

    assert artifact["evaluatedNodes"] == ["synthetic-node-3"]
    assert artifact["results"][0]["failedServices"] == ["emailservice"]


def test_demo_runner_refuses_existing_result_before_creating_inputs(
    tmp_path,
) -> None:
    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    (output_dir / RESULT_FILE).write_text("existing", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        run_disposable_demo(
            scenario_id="existing",
            output_dir=output_dir,
            failed_nodes=[],
            generated_at=GENERATED_AT,
        )

    assert sorted(path.name for path in output_dir.iterdir()) == [RESULT_FILE]


def test_cli_compact_mode_prints_summary_and_writes_bundle(
    tmp_path,
    capsys,
) -> None:
    output_dir = tmp_path / "cli-demo"

    exit_code = main(
        [
            "--scenario-id",
            "cli-demo",
            "--output-dir",
            str(output_dir),
            "--compact",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Component 3 disposable scenario: cli-demo" in captured.out
    assert "Highest-risk nodes: synthetic-node-1" in captured.out
    assert captured.err == ""
    assert (output_dir / RESULT_FILE).is_file()


def test_cli_reports_existing_bundle_without_traceback(tmp_path, capsys) -> None:
    output_dir = tmp_path / "existing-cli"
    run_disposable_demo(
        scenario_id="existing-cli",
        output_dir=output_dir,
        failed_nodes=[],
        generated_at=GENERATED_AT,
    )

    exit_code = cli(
        [
            "--scenario-id",
            "existing-cli",
            "--output-dir",
            str(output_dir),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "error: refusing to overwrite" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
