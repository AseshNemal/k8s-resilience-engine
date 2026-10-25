"""Explainable baseline-versus-intervention dependency assessment."""

from __future__ import annotations

from collections.abc import Iterable
from collections import Counter

from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    AssessmentPolicy,
    CohortStatistics,
    EdgeDependencyAssessment,
    EdgeInterventionCohort,
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
    allowed_execution_ids: set[str] | None = None,
) -> tuple[CohortStatistics, tuple[str, ...]]:
    outcomes = list(records)
    execution_counts = Counter(item.registration.execution_id for item in outcomes)
    trace_counts = Counter(trace_id for item in outcomes for trace_id in item.trace_ids)
    eligible = [
        item
        for item in outcomes
        if item.correlation_status is CorrelationStatus.MATCHED
        and item.matched_trace_id is not None
        and item.matched_trace_id == item.registration.expected_trace_id
        and execution_counts[item.registration.execution_id] == 1
        and all(trace_counts[trace_id] == 1 for trace_id in item.trace_ids)
        and item.status
        in {ExecutionOutcomeStatus.SUCCEEDED, ExecutionOutcomeStatus.FAILED}
        and (allowed_execution_ids is None or item.registration.execution_id in allowed_execution_ids)
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
            eligible_execution_ids=tuple(
                item.registration.execution_id for item in eligible
            ),
            eligible_trace_ids=tuple(
                trace_id
                for item in eligible
                for trace_id in sorted(item.trace_ids)
            ),
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


def _cohorts_overlap(baseline: CohortStatistics, intervention: CohortStatistics) -> bool:
    return bool(
        set(baseline.eligible_execution_ids) & set(intervention.eligible_execution_ids)
        or set(baseline.eligible_trace_ids) & set(intervention.eligible_trace_ids)
    )


def classify_cohort_effect(
    baseline: CohortStatistics,
    intervention: CohortStatistics,
    policy: AssessmentPolicy,
) -> tuple[ValidatedDependencyType, float | None]:
    """One decision rule for service, edge and sensitivity calculations.

    Callers still enforce intervention verification, sample and baseline gates.
    A large drop with surviving runs, or a material improvement, is unresolved.
    """
    if baseline.success_rate is None or intervention.success_rate is None:
        return ValidatedDependencyType.INSUFFICIENT_EVIDENCE, None
    effect = round(baseline.success_rate - intervention.success_rate, 4)
    if effect >= policy.mandatory_effect_threshold:
        label = (
            ValidatedDependencyType.MANDATORY
            if intervention.success_rate <= policy.max_mandatory_success_rate
            else ValidatedDependencyType.INSUFFICIENT_EVIDENCE
        )
    elif effect < -policy.optional_effect_tolerance:
        label = ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    elif effect <= policy.optional_effect_tolerance:
        label = ValidatedDependencyType.OPTIONAL
    else:
        label = ValidatedDependencyType.SUPPORTING
    return label, effect


def assess_service_dependency(
    service_name: str,
    baseline_records: list[ExecutionOutcome],
    intervention: InterventionCohort | None,
    intervention_records: list[ExecutionOutcome],
    policy: AssessmentPolicy,
    *,
    baseline_allowed_execution_ids: set[str] | None = None,
    intervention_allowed_execution_ids: set[str] | None = None,
) -> ServiceDependencyAssessment:
    """Classify one candidate without hiding weak or contradictory evidence."""
    baseline, baseline_excluded = _statistics(baseline_records, baseline_allowed_execution_ids)
    intervention_stats, intervention_excluded = _statistics(intervention_records, intervention_allowed_execution_ids)
    excluded = baseline_excluded + intervention_excluded
    reasons: list[str] = []
    if _cohorts_overlap(baseline, intervention_stats):
        reasons.append("Baseline and intervention reuse the same execution or trace evidence")

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
    effect_label, effect = classify_cohort_effect(baseline, intervention_stats, policy)

    classification = ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    if not gate_failed and effect is not None:
        if effect >= policy.mandatory_effect_threshold:
            if effect_label is ValidatedDependencyType.MANDATORY:
                classification = effect_label
                reasons.append(
                    "Confirmed intervention produced a repeatable workflow failure effect"
                )
            else:
                reasons.append(
                    "Large failure effect conflicts with successful intervention executions"
                )
        elif effect < -policy.optional_effect_tolerance:
            reasons.append("Intervention improved success materially; investigate confounding before classification")
        elif effect <= policy.optional_effect_tolerance:
            classification = effect_label
            reasons.append(
                "Confirmed intervention did not materially change workflow success"
            )
        else:
            classification = effect_label
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


def assess_edge_dependency(
    source_service: str,
    target_service: str,
    baseline_records: list[ExecutionOutcome],
    intervention: EdgeInterventionCohort | None,
    intervention_records: list[ExecutionOutcome],
    policy: AssessmentPolicy,
) -> EdgeDependencyAssessment:
    """Classify one directed call using its own controlled intervention evidence.

    Service labels are deliberately not accepted: a mandatory target service
    does not prove that every observed inbound edge to that service is mandatory.
    """
    if source_service == target_service:
        raise ValueError("Edge assessment requires different source and target services")
    if intervention is not None and (
        intervention.source_service != source_service
        or intervention.target_service != target_service
    ):
        raise ValueError("Edge intervention does not match the assessed edge")

    baseline, baseline_excluded = _statistics(baseline_records)
    intervention_stats, intervention_excluded = _statistics(intervention_records)
    excluded = baseline_excluded + intervention_excluded
    reasons: list[str] = []
    if _cohorts_overlap(baseline, intervention_stats):
        reasons.append("Baseline and intervention reuse the same execution or trace evidence")

    if intervention is None:
        reasons.append("No intervention cohort was registered for this candidate edge")
    elif not intervention.intervention_confirmed:
        reasons.append("The edge intervention was not independently confirmed")
    if baseline.eligible_records < policy.min_samples_per_cohort:
        reasons.append("Baseline cohort has fewer eligible samples than required")
    if intervention_stats.eligible_records < policy.min_samples_per_cohort:
        reasons.append("Edge intervention cohort has fewer eligible samples than required")
    if (
        baseline.success_rate is not None
        and baseline.success_rate < policy.min_baseline_success_rate
    ):
        reasons.append("Baseline success rate is below the configured stability gate")

    effect_label, effect = classify_cohort_effect(baseline, intervention_stats, policy)

    classification = ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    if not reasons and effect is not None:
        if effect >= policy.mandatory_effect_threshold:
            if effect_label is ValidatedDependencyType.MANDATORY:
                classification = effect_label
                reasons.append(
                    "Confirmed edge intervention produced a repeatable workflow failure effect"
                )
            else:
                reasons.append(
                    "Large edge failure effect conflicts with successful intervention executions"
                )
        elif effect < -policy.optional_effect_tolerance:
            reasons.append("Intervention improved success materially; investigate confounding before classification")
        elif effect <= policy.optional_effect_tolerance:
            classification = effect_label
            reasons.append(
                "Confirmed edge intervention did not materially change workflow success"
            )
        else:
            classification = effect_label
            reasons.append(
                "Edge intervention caused partial degradation below the mandatory threshold"
            )

    return EdgeDependencyAssessment(
        source_service=source_service,
        target_service=target_service,
        classification=classification,
        baseline=baseline,
        intervention=intervention_stats,
        intervention_type=intervention.intervention_type if intervention else None,
        failure_effect=effect,
        evidence_strength=_strength(baseline, intervention_stats, policy),
        reasons=tuple(reasons),
        excluded_execution_ids=excluded,
    )
