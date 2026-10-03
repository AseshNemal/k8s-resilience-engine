"""Explainable baseline-versus-intervention dependency assessment."""

from __future__ import annotations

from collections.abc import Iterable

from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    AssessmentPolicy,
    CohortStatistics,
    InterventionCohort,
    ServiceDependencyAssessment,
    ValidatedDependencyType,
)
from app.engine.workflow_dependency_modeling.execution_models import (
    CorrelationStatus,
    ExecutionOutcome,
    ExecutionOutcomeStatus,
)


def _statistics(
    records: Iterable[ExecutionOutcome],
) -> tuple[CohortStatistics, tuple[str, ...]]:
    outcomes = list(records)
    eligible = [
        item
        for item in outcomes
        if item.correlation_status is CorrelationStatus.MATCHED
        and item.status
        in {ExecutionOutcomeStatus.SUCCEEDED, ExecutionOutcomeStatus.FAILED}
    ]
    excluded = tuple(
        item.registration.execution_id for item in outcomes if item not in eligible
    )
    succeeded = sum(
        item.status is ExecutionOutcomeStatus.SUCCEEDED for item in eligible
    )
    failed = sum(item.status is ExecutionOutcomeStatus.FAILED for item in eligible)
    return (
        CohortStatistics(
            total_records=len(outcomes),
            eligible_records=len(eligible),
            excluded_records=len(excluded),
            succeeded_records=succeeded,
            failed_records=failed,
            success_rate=succeeded / len(eligible) if eligible else None,
        ),
        excluded,
    )


def _strength(
    baseline: CohortStatistics,
    intervention: CohortStatistics,
    policy: AssessmentPolicy,
) -> float:
    total = baseline.total_records + intervention.total_records
    eligible = baseline.eligible_records + intervention.eligible_records
    correlation_quality = eligible / total if total else 0.0
    sample_coverage = min(
        1.0,
        min(baseline.eligible_records, intervention.eligible_records)
        / policy.min_samples_per_cohort,
    )
    return round(correlation_quality * sample_coverage, 4)


def assess_service_dependency(
    service_name: str,
    baseline_records: list[ExecutionOutcome],
    intervention: InterventionCohort | None,
    intervention_records: list[ExecutionOutcome],
    policy: AssessmentPolicy,
) -> ServiceDependencyAssessment:
    """Classify one candidate without hiding weak or contradictory evidence."""
    baseline, baseline_excluded = _statistics(baseline_records)
    intervention_stats, intervention_excluded = _statistics(intervention_records)
    excluded = baseline_excluded + intervention_excluded
    reasons: list[str] = []

    if intervention is None:
        reasons.append("No intervention cohort was registered for this candidate service")
    elif not intervention.intervention_confirmed:
        reasons.append("The intervention was not independently confirmed")
    if baseline.eligible_records < policy.min_samples_per_cohort:
        reasons.append("Baseline cohort has fewer eligible samples than required")
    if intervention_stats.eligible_records < policy.min_samples_per_cohort:
        reasons.append("Intervention cohort has fewer eligible samples than required")
    if (
        baseline.success_rate is not None
        and baseline.success_rate < policy.min_baseline_success_rate
    ):
        reasons.append("Baseline success rate is below the configured stability gate")

    gate_failed = bool(reasons)
    effect = None
    if baseline.success_rate is not None and intervention_stats.success_rate is not None:
        effect = round(baseline.success_rate - intervention_stats.success_rate, 4)

    classification = ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    if not gate_failed and effect is not None:
        if effect >= policy.mandatory_effect_threshold:
            if intervention_stats.success_rate <= policy.max_mandatory_success_rate:
                classification = ValidatedDependencyType.MANDATORY
                reasons.append(
                    "Confirmed intervention produced a repeatable workflow failure effect"
                )
            else:
                reasons.append(
                    "Large failure effect conflicts with successful intervention executions"
                )
        elif effect <= policy.optional_effect_tolerance:
            classification = ValidatedDependencyType.OPTIONAL
            reasons.append(
                "Confirmed intervention did not materially change workflow success"
            )
        else:
            classification = ValidatedDependencyType.SUPPORTING
            reasons.append(
                "Intervention caused partial degradation below the mandatory threshold"
            )

    return ServiceDependencyAssessment(
        service_name=service_name,
        classification=classification,
        baseline=baseline,
        intervention=intervention_stats,
        intervention_type=intervention.intervention_type if intervention else None,
        failure_effect=effect,
        evidence_strength=_strength(baseline, intervention_stats, policy),
        reasons=tuple(reasons),
        excluded_execution_ids=excluded,
    )
