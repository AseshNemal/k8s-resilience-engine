import json
from datetime import datetime, timezone

import pytest

from experiments.failure_impact_validation.run_disposable_portfolio_demo import (
    COMPONENT4_HANDOFF_FILE,
    CROSS_LAYER_FILE,
    DEPLOYMENT_FILE,
    METADATA_FILE,
    RESULT_FILE,
    WORKFLOWS_FILE,
    build_disposable_portfolio_artifacts,
    format_compact_summary,
    write_disposable_portfolio_demo,
)


GENERATED_AT = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)


def _artifacts():
    return build_disposable_portfolio_artifacts(
        scenario_id="viva-portfolio",
        failed_nodes=[],
        generated_at=GENERATED_AT,
    )


def test_portfolio_demo_builds_all_cross_layer_graphs_together() -> None:
    artifacts = _artifacts()
    workflows = artifacts[WORKFLOWS_FILE]
    cross_layer = artifacts[CROSS_LAYER_FILE]

    assert workflows["evidenceStatus"] == "SYNTHETIC_DISPOSABLE"
    assert len(workflows["workflows"]) == 4
    assert cross_layer["workflowCount"] == 4
    assert len(cross_layer["graphs"]) == 4
    assert {
        graph["workflowId"] for graph in cross_layer["graphs"]
    } == {
        workflow["workflowId"] for workflow in workflows["workflows"]
    }
    assert all(not graph["unresolvedServices"] for graph in cross_layer["graphs"])
    assert {
        edge["relationship"]
        for graph in cross_layer["graphs"]
        for edge in graph["edges"]
    } >= {
        "REQUIRES_MANDATORY",
        "USES_OPTIONAL",
        "CALLS",
        "HAS_REPLICA",
        "RUNS_ON",
        "IN_FAILURE_DOMAIN",
    }


def test_portfolio_demo_produces_fractional_blast_radius() -> None:
    results = _artifacts()[RESULT_FILE]
    reports = {
        report["failedNode"]: report
        for report in results["results"]
    }

    node_1 = reports["synthetic-node-1"]
    assert len(node_1["failedWorkflows"]) == 2
    assert node_1["failedServices"] == ["cartservice"]
    assert node_1["blastRadius"] == pytest.approx(50.0)
    assert node_1["survivability"] == pytest.approx(50.0)

    node_2 = reports["synthetic-node-2"]
    assert node_2["failedWorkflows"] == []
    assert node_2["blastRadius"] == pytest.approx(0.0)
    assert node_2["survivability"] == pytest.approx(100.0)

    node_3 = reports["synthetic-node-3"]
    assert len(node_3["failedWorkflows"]) == 1
    assert node_3["failedServices"] == ["emailservice"]
    assert node_3["blastRadius"] == pytest.approx(25.0)
    assert node_3["survivability"] == pytest.approx(75.0)


def test_portfolio_demo_builds_the_real_component_4_input_contracts() -> None:
    handoff = _artifacts()[COMPONENT4_HANDOFF_FILE]

    assert handoff["evidenceStatus"] == "SYNTHETIC_DISPOSABLE"
    assert handoff["targetComponent"] == "recommendation_evaluation_validation"
    assert handoff["deploymentGraph"]["snapshotId"].startswith("synthetic-")
    assert handoff["invocationCount"] == 12
    assert len(handoff["failureImpactReports"]) == 12
    assert {
        report["failedNode"] for report in handoff["failureImpactReports"]
    } == {
        "synthetic-node-1",
        "synthetic-node-2",
        "synthetic-node-3",
    }
    assert all(
        {"workflowId", "failedNode", "blastRadius", "survivability",
         "failedServices", "failedWorkflows", "rootCauses",
         "nodeRiskRanking"} <= set(report)
        for report in handoff["failureImpactReports"]
    )


def test_portfolio_demo_writes_complete_bundle_without_overwrite(tmp_path) -> None:
    output_dir = tmp_path / "portfolio"
    artifacts = write_disposable_portfolio_demo(
        scenario_id="file-demo",
        output_dir=output_dir,
        failed_nodes=[],
        generated_at=GENERATED_AT,
    )

    assert sorted(path.name for path in output_dir.iterdir()) == sorted(
        [
            WORKFLOWS_FILE,
            DEPLOYMENT_FILE,
            METADATA_FILE,
            CROSS_LAYER_FILE,
            RESULT_FILE,
            COMPONENT4_HANDOFF_FILE,
        ]
    )
    for filename, artifact in artifacts.items():
        assert json.loads((output_dir / filename).read_text(encoding="utf-8")) == artifact

    with pytest.raises(ValueError, match="refusing to overwrite"):
        write_disposable_portfolio_demo(
            scenario_id="file-demo",
            output_dir=output_dir,
            failed_nodes=[],
            generated_at=GENERATED_AT,
        )


def test_portfolio_demo_can_select_one_node_and_format_summary() -> None:
    artifacts = build_disposable_portfolio_artifacts(
        scenario_id="selected-node",
        failed_nodes=["synthetic-node-3"],
        generated_at=GENERATED_AT,
    )
    result = artifacts[RESULT_FILE]
    summary = format_compact_summary(result)

    assert result["evaluatedNodes"] == ["synthetic-node-3"]
    assert "Workflows evaluated: 4" in summary
    assert "synthetic-node-3" in summary
    assert "25.0%" in summary
    assert "75.0%" in summary
    assert "emailservice" in summary


def test_portfolio_demo_rejects_unknown_node() -> None:
    with pytest.raises(ValueError, match="unknown failed nodes"):
        build_disposable_portfolio_artifacts(
            scenario_id="unknown-node",
            failed_nodes=["missing-node"],
            generated_at=GENERATED_AT,
        )
