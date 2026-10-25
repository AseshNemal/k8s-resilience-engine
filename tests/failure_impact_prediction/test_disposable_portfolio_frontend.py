from pathlib import Path

import pytest
from fastapi import HTTPException

from experiments.failure_impact_validation.portfolio_demo_app import (
    NodeFailureRequest,
    ScenarioRequest,
    app,
    generate_synthetic_portfolio,
    simulate_all_worker_nodes,
    simulate_one_worker_node,
)


TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "experiments"
    / "failure_impact_validation"
    / "templates"
    / "disposable_portfolio.html"
)


def test_research_frontend_is_explicitly_synthetic_and_separate() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    paths = app.openapi()["paths"]

    assert "SYNTHETIC_DISPOSABLE" in template
    assert "not observed Kubernetes evidence" in template
    assert "Generate synthetic data" in template
    assert "Fail selected node" in template
    assert "Fail all nodes independently" in template
    assert "/api/synthetic/generate" in paths
    assert "/api/synthetic/fail-one" in paths
    assert "/api/synthetic/fail-all" in paths


def test_generate_returns_four_workflows_and_all_cross_layer_graphs() -> None:
    payload = generate_synthetic_portfolio(
        ScenarioRequest(scenarioId="frontend-generate")
    )

    assert payload["metadata"]["evidenceStatus"] == "SYNTHETIC_DISPOSABLE"
    assert len(payload["workflowArtifact"]["workflows"]) == 4
    assert payload["crossLayerArtifact"]["workflowCount"] == 4
    assert len(payload["crossLayerArtifact"]["graphs"]) == 4
    assert "resultArtifact" not in payload


def test_fail_all_returns_fractional_portfolio_results() -> None:
    payload = simulate_all_worker_nodes(
        ScenarioRequest(scenarioId="frontend-all")
    )

    results = {
        result["failedNode"]: result
        for result in payload["resultArtifact"]["results"]
    }
    assert results["synthetic-node-1"]["blastRadius"] == 50.0
    assert results["synthetic-node-2"]["blastRadius"] == 0.0
    assert results["synthetic-node-3"]["blastRadius"] == 25.0
    assert payload["component4Handoff"]["invocationCount"] == 12
    assert len(payload["component4Handoff"]["failureImpactReports"]) == 12


def test_fail_one_returns_only_the_selected_worker_node() -> None:
    payload = simulate_one_worker_node(
        NodeFailureRequest(
            scenarioId="frontend-single",
            failedNode="synthetic-node-3",
        )
    )

    result_artifact = payload["resultArtifact"]
    assert result_artifact["evaluatedNodes"] == ["synthetic-node-3"]
    assert len(result_artifact["results"]) == 1
    assert result_artifact["results"][0]["blastRadius"] == 25.0
    assert payload["component4Handoff"]["invocationCount"] == 4


def test_fail_one_rejects_an_unknown_worker_node() -> None:
    with pytest.raises(HTTPException) as captured:
        simulate_one_worker_node(
            NodeFailureRequest(
                scenarioId="frontend-unknown",
                failedNode="missing-node",
            )
        )

    assert captured.value.status_code == 422
    assert "unknown failed nodes" in captured.value.detail
