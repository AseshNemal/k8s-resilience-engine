"""Operational trust must be explicit, current and scoped; history is never promoted."""
import pytest
from app.engine.workflow_dependency_modeling import service, management, operational_evidence as policy
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.workflow_management import OperationalWorkflowReview
from tests.workflow_dependency_modeling.test_workflow_management import editor, edit


def review(row, **changes):
    return OperationalWorkflowReview.model_validate({"workflowId": row["id"], "kind": "managed",
        "sourceVersion": str(row["version"]), "namespace": "shop", "connectionFingerprint": "fake-connection",
        "expectedVersion": 0, "actor": "Owner", "statement": "Checked the actual order and payment implementation; both services are required.", **changes})


def test_legacy_reviewed_definition_is_preserved_but_never_automatically_trusted(editor):
    repo, _, _ = editor
    row = management.save_workflow(edit(reviewed=True))
    assert service.list_operational_workflows("shop", "fake-connection") == []
    with pytest.raises(EvidenceError, match="current review"):
        service.get_operational_workflow(row["id"], "shop")
    assert repo.latest("workflow", row["id"]) == row


def test_explicit_review_pins_revision_uid_namespace_and_does_not_claim_observation(editor, monkeypatch):
    repo, _, client = editor
    monkeypatch.setattr(policy, "current_cluster_uid", lambda: "disposable-uid")
    row = management.save_workflow(edit(reviewed=True))
    response = client.post("/api/workflows/management/operational-reviews", json=review(row).model_dump(by_alias=True))
    assert response.status_code == 200, response.text
    record = response.json()
    assert record["basis"] == "operator-reviewed" and not record["observedDependencies"]
    item = service.get_operational_workflow(row["id"], "shop")
    assert item.graph.mandatory_services == ["orders", "payments"]
    assert item.operational_review["sourceVersion"] == "1"
    assert not service.list_operational_workflows("other", "fake-connection")
    assert not service.list_operational_workflows("shop", "different-connection")
    monkeypatch.setattr(policy, "current_cluster_uid", lambda: "replacement-uid")
    assert not service.list_operational_workflows("shop", "fake-connection")
    assert len(repo.versions("operational-review")) == 1


def test_edit_including_rename_requires_new_review_and_retains_old_evidence(editor, monkeypatch):
    repo, _, _ = editor
    monkeypatch.setattr(policy, "current_cluster_uid", lambda: "uid")
    row = management.save_workflow(edit(reviewed=True))
    record = service.review_operational_workflow(review(row))
    changed = management.save_workflow(edit(reviewed=True, expectedVersion=1, name="Changed"), row["id"])
    assert not service.list_operational_workflows("shop", "fake-connection")
    with pytest.raises(EvidenceError, match="definition changed"):
        service.review_operational_workflow(review(row, expectedVersion=1))
    new = service.review_operational_workflow(review(changed, expectedVersion=1))
    assert new["sourceDigest"] != record["sourceDigest"]
    assert len(repo.versions("operational-review")) == 2
    assert len(repo.versions("workflow", row["id"])) == 2


def test_unknown_uid_and_nonreviewed_definitions_fail_closed(editor, monkeypatch):
    row = management.save_workflow(edit())
    with pytest.raises(EvidenceError, match="reviewed workflow"):
        service.review_operational_workflow(review(row))
    row = management.save_workflow(edit(reviewed=True))
    monkeypatch.setattr(policy, "current_cluster_uid", lambda: (_ for _ in ()).throw(policy._error("UID unknown")))
    with pytest.raises(EvidenceError, match="UID unknown"):
        service.review_operational_workflow(review(row))


def test_reviewed_real_definition_reaches_recommendation_and_persisted_approval_with_accurate_provenance(editor, monkeypatch):
    from app.engine.recommendation_evaluation_validation.live_pipeline import build_live_recommendation
    from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
    from app.engine.recommendation_evaluation_validation.approval import ApprovalService, has_verified_provenance
    from tests.support.member4_approvals import InMemoryApprovals
    repo, cluster, client = editor
    cluster.version = "v1.36.1"
    cluster.template_specs = {name: {"containers": [{"name": name, "resources": {"requests": {"cpu": "50m", "memory": "32Mi"}}}]} for name in ("orders", "payments")}
    monkeypatch.setattr(policy, "current_cluster_uid", lambda: "disposable-uid")
    row = management.save_workflow(edit(reviewed=True))
    before = client.get("/api/recommendations/context?namespace=shop").json()
    assert not before["ready"] and before["workflows"] == []
    service.review_operational_workflow(review(row))
    context = client.get("/api/recommendations/context?namespace=shop").json()
    assert context["ready"] and context["workflowId"] == row["id"]
    live = build_live_recommendation(row["id"], namespace="shop")
    assert live.evidence["workflowReview"]["basis"] == "operator-reviewed"
    assert live.evidence["isPrediction"] and not live.evidence["workflowReview"]["observedDependencies"]
    selected = live.report.selected_recommendation_id
    assert selected
    proposal = build_yaml_proposal(live, selected)
    gate = ApprovalService(InMemoryApprovals())
    pending = gate.request_approval(selected, "1", live.report, requested_by="Owner", evidence=live.evidence, proposal_yaml=proposal.yaml)
    assert has_verified_provenance(pending)
    approved = gate.approve(selected, "1", "Reviewed change", decided_by="Owner", evaluate=lambda _: live)
    assert approved.evaluation_snapshot["provenance"]["workflowReview"]["clusterUid"] == "disposable-uid"
    assert not any(verb in call for call in cluster.calls for verb in ("patch", "delete", "update", "apply"))
