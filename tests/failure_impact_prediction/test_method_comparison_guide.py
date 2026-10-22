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


def test_guide_records_required_comparison_provenance() -> None:
    guide = GUIDE.read_text(encoding="utf-8")

    assert "$workflowFreezeId = $frozenWorkflow.freezeId" in guide
    assert "$deploymentSnapshotId = $deploymentGraph.snapshotId" in guide
    assert "--workflow-freeze-id $workflowFreezeId" in guide
    assert "--deployment-snapshot-id $deploymentSnapshotId" in guide
    assert "mandatory for this\nresearch workflow" in guide


def test_guide_documents_post_failure_method_evaluation() -> None:
    guide = GUIDE.read_text(encoding="utf-8")

    assert "run_method_evaluation.py" in guide
    assert "--comparison $comparisonPath" in guide
    assert "--evidence $evidencePath" in guide
    assert "--output $methodEvaluationPath" in guide
    assert "claims were not probed" in guide
    assert "exported as `null`" in guide
