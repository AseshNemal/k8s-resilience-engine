from app.engine.deployment_topology_modeling.service import discover_live
from app.engine.recommendation_evaluation_validation import deployment_recommendations as subject
from app.shared.evidence_errors import EvidenceError
from tests.recommendation_evaluation_validation.test_independent_inputs import concentrated_db
import pytest


def test_unknown_workflows_still_get_real_placement_simulation_without_synthesized_dependencies(use_cluster, monkeypatch):
    cluster = use_cluster(concentrated_db())
    discovered = discover_live(namespace="shop")
    before = discovered.graph.model_dump()
    predict = subject.predict_deployment_failure_impact
    calls = []
    def member_three(graph, failed_node):
        calls.append(failed_node)
        return predict(graph, failed_node)
    monkeypatch.setattr(subject, "predict_deployment_failure_impact", member_three)
    result = subject.build_deployment_recommendations(discovered)
    assert calls and result["isPrediction"]
    assert result["workflowSurvival"] is None and result["workflowBlastRadius"] is None
    assert not result["approvalAllowed"]
    assert result["selectedCandidateId"]
    assert any(r["feasible"] and r["worstCaseAfter"] < r["worstCaseBefore"] for r in result["recommendations"])
    assert all(r["workflowSurvival"] is None and not r["approvalAllowed"] for r in result["recommendations"])
    assert before == discovered.graph.model_dump()
    assert not any(verb in call for call in cluster.calls for verb in ("patch", "apply", "delete", "update"))


def test_incomplete_placement_never_yields_a_verified_deployment_recommendation(use_cluster):
    use_cluster(concentrated_db())
    discovered = discover_live(namespace="shop")
    discovered.service_evidence["db"].complete = False
    with pytest.raises(EvidenceError, match="Complete placement"):
        subject.build_deployment_recommendations(discovered)
