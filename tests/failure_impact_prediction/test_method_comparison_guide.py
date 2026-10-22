from pathlib import Path


GUIDE = (
    Path(__file__).resolve().parents[2]
    / "experiments"
    / "failure_impact_validation"
    / "VALIDATION_GUIDE.txt"
)


def test_guide_documents_real_upstream_comparison_inputs() -> None:
    guide = GUIDE.read_text(encoding="utf-8")

    assert "/api/workflows/$escapedWorkflowId/frozen" in guide
    assert "/api/deployments/live?namespace=$escapedNamespace" in guide
    assert "$frozenWorkflow.graph | ConvertTo-Json" in guide
    assert "Do not replace an unavailable frozen workflow" in guide


def test_guide_documents_complete_method_comparison_command() -> None:
    guide = GUIDE.read_text(encoding="utf-8")

    assert "run_method_comparison.py" in guide
    assert "--workflow $workflowPath" in guide
    assert "--deployment $deploymentPath" in guide
    assert "--failed-node $failedNode" in guide
    assert "--dependency-scenario-service $dependencyScenarioService" in guide
    assert "--output $comparisonPath" in guide


def test_guide_declares_dependency_scenario_before_observation() -> None:
    guide = GUIDE.read_text(encoding="utf-8")

    declaration = guide.index(
        "Service declared unavailable in the dependency-only scenario"
    )
    failure_experiment = guide.index(
        "## 3. Conduct and observe the controlled experiment"
    )

    assert declaration < failure_experiment
    assert "Never populate it from the\nfailures observed" in guide
