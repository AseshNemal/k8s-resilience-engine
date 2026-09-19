from app.engine.recommendation_evaluation_validation.candidate_generation.candidates.topology_spread_candidate import (
    TopologySpreadCandidateBuilder,
)
from app.engine.recommendation_evaluation_validation.candidate_generation.models import (
    CandidateContext,
)
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.failure_impact import FailureImpactReport, RootCause


def build_context(root_causes: list[RootCause]) -> CandidateContext:
    failure_impact = FailureImpactReport(
        workflow_id="wf-1",
        failed_node="node-1",
        blast_radius=0.5,
        survivability=0.5,
        failed_services=["service-a"],
        failed_workflows=["wf-1"],
        root_causes=root_causes,
    )

    deployment = DeploymentGraph(nodes=[], services=[])

    return CandidateContext(deployment=deployment, failure_impact=failure_impact)


def test_replica_concentration_generates_topology_spread_candidate() -> None:
    context = build_context(
        [
            RootCause(
                type="REPLICA_CONCENTRATION",
                service="service-a",
                failure_domain="zone-a",
                concentration=0.67,
            )
        ]
    )

    candidates = TopologySpreadCandidateBuilder().build(context)

    assert len(candidates) == 1
    assert candidates[0].recommendation_type == "TOPOLOGY_SPREAD"


def test_correct_service_is_used() -> None:
    context = build_context(
        [
            RootCause(
                type="REPLICA_CONCENTRATION",
                service="checkout-service",
                failure_domain="zone-b",
                concentration=0.5,
            )
        ]
    )

    candidates = TopologySpreadCandidateBuilder().build(context)

    assert candidates[0].service == "checkout-service"


def test_concentration_is_included_in_reason_when_available() -> None:
    context = build_context(
        [
            RootCause(
                type="REPLICA_CONCENTRATION",
                service="service-a",
                failure_domain="zone-a",
                concentration=0.6666,
            )
        ]
    )

    candidates = TopologySpreadCandidateBuilder().build(context)

    assert "66.66%" in candidates[0].reason
    assert "zone-a" in candidates[0].reason


def test_reason_omits_percentage_when_concentration_is_none() -> None:
    context = build_context(
        [
            RootCause(
                type="REPLICA_CONCENTRATION",
                service="service-a",
                failure_domain="zone-a",
                concentration=None,
            )
        ]
    )

    candidates = TopologySpreadCandidateBuilder().build(context)

    assert "%" not in candidates[0].reason
    assert "service-a" in candidates[0].reason
    assert "zone-a" in candidates[0].reason


def test_non_replica_concentration_cause_does_not_generate_candidate() -> None:
    context = build_context(
        [
            RootCause(
                type="SINGLE_POINT_OF_FAILURE",
                service="service-a",
                failure_domain="zone-a",
                concentration=None,
            )
        ]
    )

    candidates = TopologySpreadCandidateBuilder().build(context)

    assert candidates == []
