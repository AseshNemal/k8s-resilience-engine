"""Research-validity regressions for Phase 1 data separation."""

import json
from pathlib import Path

import pytest

from app.engine.workflow_dependency_modeling import fixture_repository
from app.engine.workflow_dependency_modeling.evaluator import evaluate_graph
from app.engine.workflow_dependency_modeling.fixture_repository import (
    get_reference,
    get_scenario,
    load_fixture_traces,
)
from app.engine.workflow_dependency_modeling.models import (
    ComponentDataError,
    ReferenceWorkflow,
    WorkflowScenario,
)
from app.engine.workflow_dependency_modeling.service import build_workflow_graph


def changed_reference(workflow_id: str) -> ReferenceWorkflow:
    return ReferenceWorkflow.from_mapping(
        {
            "workflowId": workflow_id,
            "expectedServices": ["unrelated-service", "missing-service"],
            "mandatoryServices": ["unrelated-service", "missing-service"],
            "optionalServices": [],
            "expectedEdges": [
                {"source": "unrelated-service", "target": "missing-service"}
            ],
            "mandatoryEdges": [
                {"source": "unrelated-service", "target": "missing-service"}
            ],
            "optionalEdges": [],
            "synthetic": True,
        }
    )


def test_reference_change_affects_evaluation_not_prediction() -> None:
    scenario = get_scenario("checkout")
    traces = load_fixture_traces("checkout")

    before = build_workflow_graph(scenario, traces)
    original_evaluation = evaluate_graph(before, get_reference("checkout"))
    changed_evaluation = evaluate_graph(before, changed_reference("checkout"))
    after = build_workflow_graph(scenario, traces)

    assert after == before
    assert original_evaluation.service_metrics.f1 == pytest.approx(1.0)
    assert changed_evaluation.service_metrics.f1 == pytest.approx(0.0)


def test_scenario_rejects_reference_and_evidence_fields() -> None:
    base = {
        "workflowId": "checkout",
        "description": "Checkout",
        "criticality": 0.9,
        "successCondition": {
            "protocol": "http",
            "terminalService": "frontend",
            "operationName": "POST /checkout",
            "httpStatusCodes": [200],
        },
        "entryService": "frontend",
    }
    for forbidden in ("expectedServices", "expectedEdges", "traceFile"):
        with pytest.raises(ComponentDataError, match="cannot contain"):
            WorkflowScenario.from_mapping({**base, forbidden: []})


def test_reference_requires_explicit_expected_sets() -> None:
    with pytest.raises(ComponentDataError, match="requires expectedServices"):
        ReferenceWorkflow.from_mapping(
            {"workflowId": "checkout", "synthetic": True}
        )


def test_missing_evidence_file_never_falls_back_to_checkout(
    tmp_path, monkeypatch
) -> None:
    scenario_dir = tmp_path / "scenarios"
    evidence_dir = tmp_path / "evidence"
    scenario_dir.mkdir()
    evidence_dir.mkdir()
    scenario_dir.joinpath("missing.json").write_text(
        json.dumps(
            {
                "workflowId": "missing",
                "description": "Missing evidence fixture",
                "criticality": 0.5,
                "successCondition": {
                    "protocol": "http",
                    "terminalService": "frontend",
                    "operationName": "POST /missing",
                    "httpStatusCodes": [200],
                },
                "entryService": "frontend",
            }
        ),
        encoding="utf-8",
    )
    evidence_dir.joinpath("missing.json").write_text(
        json.dumps(
            {
                "workflowId": "missing",
                "sourceKind": "synthetic_fixture",
                "traceFile": "sample_data/traces/does-not-exist.json",
                "synthetic": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fixture_repository, "SCENARIOS_DIR", scenario_dir)
    monkeypatch.setattr(fixture_repository, "EVIDENCE_DIR", evidence_dir)

    with pytest.raises(FileNotFoundError, match="does-not-exist"):
        fixture_repository.load_fixture_traces("missing")


def test_trace_derived_names_are_not_inserted_as_html() -> None:
    script = Path("app/static/js/components/workflows.js").read_text(encoding="utf-8")
    assert "${nodeName}</text>" not in script
    assert "<td><code>${edge.source}" not in script
    assert "nameText.textContent = nodeName" in script
    assert "sourceCode.textContent = edge.source" in script
