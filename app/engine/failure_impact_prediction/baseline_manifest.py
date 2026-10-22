"""Strict parser for saved failure-impact method-comparison artifacts."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from app.engine.failure_impact_prediction.baseline_analysis import (
    DependencyOnlyBaseline,
    DeploymentExposureBaseline,
    ReplicaCountBaseline,
)
from app.engine.failure_impact_prediction.baseline_comparison import (
    FailureImpactMethodComparison,
)
from app.engine.failure_impact_prediction.baseline_export import (
    ARTIFACT_TYPE,
    METHODOLOGY_VERSION,
)
from app.shared.schemas.failure_impact import FailureImpactReport


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _ReplicaPayload(_StrictModel):
    evidence_scope: StrictStr = Field(alias="evidenceScope")
    unavailable_services: tuple[StrictStr, ...] = Field(alias="unavailableServices")
    single_replica_services: tuple[StrictStr, ...] = Field(alias="singleReplicaServices")
    vulnerable_services: tuple[StrictStr, ...] = Field(alias="vulnerableServices")
    vulnerable_service_percentage: float = Field(
        alias="vulnerableServicePercentage",
        ge=0,
        le=100,
    )


class _DeploymentPayload(_StrictModel):
    evidence_scope: StrictStr = Field(alias="evidenceScope")
    node_induced_failures: tuple[StrictStr, ...] = Field(alias="nodeInducedFailures")
    preexisting_unavailable_services: tuple[StrictStr, ...] = Field(
        alias="preexistingUnavailableServices"
    )
    unavailable_services: tuple[StrictStr, ...] = Field(alias="unavailableServices")
    unavailable_service_percentage: float = Field(
        alias="unavailableServicePercentage",
        ge=0,
        le=100,
    )


class _DependencyPayload(_StrictModel):
    evidence_scope: StrictStr = Field(alias="evidenceScope")
    unavailable_services: tuple[StrictStr, ...] = Field(alias="unavailableServices")
    failed_mandatory_services: tuple[StrictStr, ...] = Field(
        alias="failedMandatoryServices"
    )
    unavailable_optional_services: tuple[StrictStr, ...] = Field(
        alias="unavailableOptionalServices"
    )
    unreferenced_services: tuple[StrictStr, ...] = Field(alias="unreferencedServices")
    survives: StrictBool


class _ProposedPayload(_StrictModel):
    evidence_scope: StrictStr = Field(alias="evidenceScope")
    workflow_id: StrictStr = Field(alias="workflowId")
    failed_node: StrictStr = Field(alias="failedNode")
    blast_radius: float = Field(alias="blastRadius", ge=0, le=100)
    survivability: float = Field(ge=0, le=100)
    failed_services: tuple[StrictStr, ...] = Field(alias="failedServices")
    failed_workflows: tuple[StrictStr, ...] = Field(alias="failedWorkflows")
    root_causes: tuple[dict[str, object], ...] = Field(alias="rootCauses")
    node_risk_ranking: tuple[dict[str, object], ...] = Field(alias="nodeRiskRanking")


class _MethodsPayload(_StrictModel):
    replica_count_only: _ReplicaPayload = Field(alias="replicaCountOnly")
    deployment_only: _DeploymentPayload = Field(alias="deploymentOnly")
    dependency_only: _DependencyPayload = Field(alias="dependencyOnly")
    proposed_cross_layer: _ProposedPayload = Field(alias="proposedCrossLayer")


class _ArtifactPayload(_StrictModel):
    artifact_type: Literal[ARTIFACT_TYPE] = Field(alias="artifactType")
    methodology_version: Literal[METHODOLOGY_VERSION] = Field(
        alias="methodologyVersion"
    )
    generated_at: datetime = Field(alias="generatedAt")
    workflow_id: StrictStr = Field(alias="workflowId")
    deployment_snapshot_id: StrictStr = Field(alias="deploymentSnapshotId")
    failed_node: StrictStr = Field(alias="failedNode")
    dependency_scenario_services: tuple[StrictStr, ...] = Field(
        alias="dependencyScenarioServices"
    )
    methods: _MethodsPayload


@dataclass(frozen=True)
class ParsedMethodComparisonArtifact:
    """Validated comparison plus the time at which it was generated."""

    generated_at: datetime
    comparison: FailureImpactMethodComparison


def _identifiers(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if any(not value.strip() or value != value.strip() for value in values):
        raise ValueError(
            f"{field_name} must contain trimmed non-empty strings"
        )
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


def _required_text(value: str, field_name: str) -> str:
    if not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be a trimmed non-empty string")
    return value


def parse_method_comparison_artifact(
    payload: object,
) -> ParsedMethodComparisonArtifact:
    """Parse only the exact artifact shape produced by Component 3."""

    parsed = _ArtifactPayload.model_validate(payload)
    if parsed.generated_at.tzinfo is None or parsed.generated_at.utcoffset() is None:
        raise ValueError("generatedAt must include timezone information")

    workflow_id = _required_text(parsed.workflow_id, "workflowId")
    snapshot_id = _required_text(
        parsed.deployment_snapshot_id,
        "deploymentSnapshotId",
    )
    failed_node = _required_text(parsed.failed_node, "failedNode")
    scenario = _identifiers(
        parsed.dependency_scenario_services,
        "dependencyScenarioServices",
    )

    methods = parsed.methods
    replica = methods.replica_count_only
    deployment = methods.deployment_only
    dependency = methods.dependency_only
    proposed_payload = methods.proposed_cross_layer
    proposed = FailureImpactReport.model_validate(
        proposed_payload.model_dump(
            by_alias=True,
            exclude={"evidence_scope"},
        )
    )

    comparison = FailureImpactMethodComparison(
        workflow_id=workflow_id,
        deployment_snapshot_id=snapshot_id,
        failed_node=failed_node,
        dependency_scenario_services=scenario,
        replica_count_only=ReplicaCountBaseline(
            deployment_snapshot_id=snapshot_id,
            unavailable_services=_identifiers(
                replica.unavailable_services,
                "replicaCountOnly.unavailableServices",
            ),
            single_replica_services=_identifiers(
                replica.single_replica_services,
                "replicaCountOnly.singleReplicaServices",
            ),
            vulnerable_services=_identifiers(
                replica.vulnerable_services,
                "replicaCountOnly.vulnerableServices",
            ),
            vulnerable_service_percentage=replica.vulnerable_service_percentage,
        ),
        deployment_only=DeploymentExposureBaseline(
            deployment_snapshot_id=snapshot_id,
            failed_node=failed_node,
            node_induced_failures=_identifiers(
                deployment.node_induced_failures,
                "deploymentOnly.nodeInducedFailures",
            ),
            preexisting_unavailable_services=_identifiers(
                deployment.preexisting_unavailable_services,
                "deploymentOnly.preexistingUnavailableServices",
            ),
            unavailable_services=_identifiers(
                deployment.unavailable_services,
                "deploymentOnly.unavailableServices",
            ),
            unavailable_service_percentage=deployment.unavailable_service_percentage,
        ),
        dependency_only=DependencyOnlyBaseline(
            workflow_id=workflow_id,
            unavailable_services=_identifiers(
                dependency.unavailable_services,
                "dependencyOnly.unavailableServices",
            ),
            failed_mandatory_services=_identifiers(
                dependency.failed_mandatory_services,
                "dependencyOnly.failedMandatoryServices",
            ),
            unavailable_optional_services=_identifiers(
                dependency.unavailable_optional_services,
                "dependencyOnly.unavailableOptionalServices",
            ),
            unreferenced_services=_identifiers(
                dependency.unreferenced_services,
                "dependencyOnly.unreferencedServices",
            ),
            survives=dependency.survives,
        ),
        proposed_cross_layer=proposed,
    )

    if comparison.dependency_scenario_services != (
        comparison.dependency_only.unavailable_services
    ):
        raise ValueError(
            "dependencyScenarioServices must match dependencyOnly.unavailableServices"
        )
    if proposed.workflow_id != workflow_id:
        raise ValueError("proposedCrossLayer must use the artifact workflowId")
    if proposed.failed_node != failed_node:
        raise ValueError("proposedCrossLayer must use the artifact failedNode")

    return ParsedMethodComparisonArtifact(
        generated_at=parsed.generated_at,
        comparison=comparison,
    )


__all__ = [
    "ParsedMethodComparisonArtifact",
    "parse_method_comparison_artifact",
]
