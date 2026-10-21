"""Focused contract tests for explicit assessment classification groups."""

from datetime import datetime, timezone

from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    AssessmentApproval,
    AssessmentPolicy,
    CohortStatistics,
    DependencyAssessment,
    ServiceDependencyAssessment,
    ValidatedDependencyType,
)
from app.shared.schemas.workflow import WorkflowGraph


def _statistics() -> CohortStatistics:
    return CohortStatistics(3, 3, 0, 3, 0, 1.0)


def _service(
    name: str, classification: ValidatedDependencyType
) -> ServiceDependencyAssessment:
    return ServiceDependencyAssessment(
        service_name=name,
        classification=classification,
        baseline=_statistics(),
        intervention=_statistics(),
        intervention_type=None,
        failure_effect=0.0,
        evidence_strength=1.0,
        reasons=("controlled evidence",),
    )


def test_assessment_and_approval_expose_all_three_service_groups() -> None:
    graph = WorkflowGraph(
        workflow_id="checkout",
        criticality=0.9,
        mandatory_services=["paymentservice"],
        optional_services=["emailservice"],
        edges=[],
    )
    assessment = DependencyAssessment(
        assessment_id="assessment:groups:1",
        workflow_id="checkout",
        scenario_version=1,
        environment_id="test-cluster-a",
        source="controlled_experiment",
        candidate_graph=graph,
        structural_entry_service="frontend",
        baseline_batch_ids=("batch:baseline",),
        interventions=(),
        edge_interventions=(),
        policy=AssessmentPolicy(),
        services=(
            _service("paymentservice", ValidatedDependencyType.MANDATORY),
            _service("inventoryservice", ValidatedDependencyType.SUPPORTING),
            _service("emailservice", ValidatedDependencyType.OPTIONAL),
        ),
        edges=(),
        created_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
        created_by="test",
    )

    payload = assessment.to_mapping()
    assert payload["mandatoryServices"] == ["paymentservice"]
    assert payload["supportingServices"] == ["inventoryservice"]
    assert payload["optionalServices"] == ["emailservice"]

    approval = AssessmentApproval(
        assessment_id=assessment.assessment_id,
        workflow_id="checkout",
        scenario_version=1,
        approved_graph=graph,
        approved_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
        approved_by="reviewer",
        rationale="Reviewed evidence",
        excluded_supporting_services=("inventoryservice",),
    )
    approved_payload = approval.to_mapping()
    assert approved_payload["mandatoryServices"] == ["paymentservice"]
    assert approved_payload["supportingServices"] == ["inventoryservice"]
    assert approved_payload["optionalServices"] == ["emailservice"]
