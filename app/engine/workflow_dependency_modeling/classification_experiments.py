"""Repeatability and threshold-sensitivity analysis for frozen assessments."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isclose
from typing import Any, Mapping, Sequence

from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    AssessmentPolicy,
    DependencyAssessment,
    ServiceDependencyAssessment,
    ValidatedDependencyType,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_repository import (
    DependencyAssessmentRepository,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_service import (
    get_dependency_assessment,
)


@dataclass(frozen=True)
class SensitivityCase:
    case_id: str
    policy: AssessmentPolicy
    classifications: dict[str, str]
    changed_services: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "caseId": self.case_id,
            "policy": self.policy.to_mapping(),
            "classifications": self.classifications,
            "changedServices": list(self.changed_services),
        }


@dataclass(frozen=True)
class SensitivityReport:
    assessment_id: str
    workflow_id: str
    baseline_classifications: dict[str, str]
    cases: tuple[SensitivityCase, ...]
    robust_services: tuple[str, ...]
    sensitive_services: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "assessmentId": self.assessment_id,
            "workflowId": self.workflow_id,
            "baselineClassifications": self.baseline_classifications,
            "cases": [case.to_mapping() for case in self.cases],
            "robustServices": list(self.robust_services),
            "sensitiveServices": list(self.sensitive_services),
        }


@dataclass(frozen=True)
class ServiceRepeatability:
    service_name: str
    classifications: dict[str, int]
    agreement_rate: float
    stable: bool

    def to_mapping(self) -> dict[str, Any]:
        return {
            "serviceName": self.service_name,
            "classifications": self.classifications,
            "agreementRate": self.agreement_rate,
            "stable": self.stable,
        }


@dataclass(frozen=True)
class RepeatabilityReport:
    workflow_id: str
    scenario_version: int
    environment_id: str
    assessment_ids: tuple[str, ...]
    services: tuple[ServiceRepeatability, ...]
    mean_agreement_rate: float
    stable_service_rate: float

    def to_mapping(self) -> dict[str, Any]:
        return {
            "workflowId": self.workflow_id,
            "scenarioVersion": self.scenario_version,
            "environmentId": self.environment_id,
            "assessmentIds": list(self.assessment_ids),
            "services": [service.to_mapping() for service in self.services],
            "meanAgreementRate": self.mean_agreement_rate,
            "stableServiceRate": self.stable_service_rate,
        }


def _summary_classification(
    result: ServiceDependencyAssessment,
    assessment: DependencyAssessment,
    policy: AssessmentPolicy,
) -> ValidatedDependencyType:
    intervention = next(
        (
            cohort
            for cohort in assessment.interventions
            if cohort.target_service == result.service_name
        ),
        None,
    )
    if intervention is None or not intervention.intervention_confirmed:
        return ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    if result.baseline.eligible_records < policy.min_samples_per_cohort:
        return ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    if result.intervention.eligible_records < policy.min_samples_per_cohort:
        return ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    if (
        result.baseline.success_rate is None
        or result.baseline.success_rate < policy.min_baseline_success_rate
        or result.intervention.success_rate is None
    ):
        return ValidatedDependencyType.INSUFFICIENT_EVIDENCE

    effect = result.baseline.success_rate - result.intervention.success_rate
    if effect >= policy.mandatory_effect_threshold:
        if result.intervention.success_rate <= policy.max_mandatory_success_rate:
            return ValidatedDependencyType.MANDATORY
        return ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    if effect <= policy.optional_effect_tolerance:
        return ValidatedDependencyType.OPTIONAL
    return ValidatedDependencyType.SUPPORTING


def analyze_threshold_sensitivity(
    assessment: DependencyAssessment,
    cases: Sequence[tuple[str, AssessmentPolicy]],
) -> SensitivityReport:
    """Reclassify frozen cohort summaries without rerunning or mutating evidence."""
    if not cases:
        raise ValueError("At least one sensitivity policy is required")
    case_ids = [case_id for case_id, _ in cases]
    if any(not case_id.strip() for case_id in case_ids):
        raise ValueError("Sensitivity case IDs must be non-empty")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Sensitivity case IDs must be unique")

    baseline = {
        result.service_name: result.classification.value
        for result in assessment.services
    }
    sensitivity_cases: list[SensitivityCase] = []
    sensitive: set[str] = set()
    for case_id, policy in cases:
        classifications = {
            result.service_name: _summary_classification(
                result, assessment, policy
            ).value
            for result in assessment.services
        }
        changed = tuple(
            sorted(
                service
                for service, classification in classifications.items()
                if baseline.get(service) != classification
            )
        )
        sensitive.update(changed)
        sensitivity_cases.append(
            SensitivityCase(
                case_id=case_id.strip(),
                policy=policy,
                classifications=classifications,
                changed_services=changed,
            )
        )
    return SensitivityReport(
        assessment_id=assessment.assessment_id,
        workflow_id=assessment.workflow_id,
        baseline_classifications=baseline,
        cases=tuple(sensitivity_cases),
        robust_services=tuple(sorted(set(baseline) - sensitive)),
        sensitive_services=tuple(sorted(sensitive)),
    )


def analyze_repeatability(
    assessments: Sequence[DependencyAssessment],
) -> RepeatabilityReport:
    """Measure label agreement across independent immutable assessment runs."""
    if len(assessments) < 2:
        raise ValueError("Repeatability analysis requires at least two assessments")
    first = assessments[0]
    identity = (first.workflow_id, first.scenario_version, first.environment_id)
    if any(
        (item.workflow_id, item.scenario_version, item.environment_id) != identity
        for item in assessments[1:]
    ):
        raise ValueError(
            "Repeatability assessments must share workflow, scenario, and environment"
        )
    assessment_ids = [item.assessment_id for item in assessments]
    if len(assessment_ids) != len(set(assessment_ids)):
        raise ValueError("Repeatability assessment IDs must be unique")

    observed = [
        {result.service_name: result.classification.value for result in item.services}
        for item in assessments
    ]
    service_names = sorted({name for labels in observed for name in labels})
    services: list[ServiceRepeatability] = []
    for service_name in service_names:
        counts = Counter(labels.get(service_name, "missing") for labels in observed)
        agreement = max(counts.values()) / len(assessments)
        services.append(
            ServiceRepeatability(
                service_name=service_name,
                classifications=dict(sorted(counts.items())),
                agreement_rate=agreement,
                stable=isclose(agreement, 1.0),
            )
        )
    mean_agreement = (
        sum(service.agreement_rate for service in services) / len(services)
        if services
        else 0.0
    )
    stable_rate = (
        sum(service.stable for service in services) / len(services)
        if services
        else 0.0
    )
    return RepeatabilityReport(
        workflow_id=first.workflow_id,
        scenario_version=first.scenario_version,
        environment_id=first.environment_id,
        assessment_ids=tuple(assessment_ids),
        services=tuple(services),
        mean_agreement_rate=mean_agreement,
        stable_service_rate=stable_rate,
    )


def run_dependency_sensitivity_analysis(
    data: Mapping[str, Any],
    *,
    assessment_repository: DependencyAssessmentRepository | None = None,
) -> SensitivityReport:
    assessment_id = str(data.get("assessmentId", "")).strip()
    raw_cases = data.get("cases")
    if not assessment_id or not isinstance(raw_cases, list):
        raise ValueError("assessmentId and cases are required")
    assessment = get_dependency_assessment(
        assessment_id, repository=assessment_repository
    )
    cases = [
        (
            str(item.get("caseId", "")),
            AssessmentPolicy.from_mapping(item.get("policy")),
        )
        for item in raw_cases
    ]
    return analyze_threshold_sensitivity(assessment, cases)


def run_dependency_repeatability_analysis(
    data: Mapping[str, Any],
    *,
    assessment_repository: DependencyAssessmentRepository | None = None,
) -> RepeatabilityReport:
    raw_ids = data.get("assessmentIds")
    if not isinstance(raw_ids, list):
        raise ValueError("assessmentIds must be a list")
    assessments = [
        get_dependency_assessment(str(assessment_id), repository=assessment_repository)
        for assessment_id in raw_ids
    ]
    return analyze_repeatability(assessments)
