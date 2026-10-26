"""Explainable baseline-versus-intervention dependency assessment.

How a service is labelled. The workflow is run many times with every service available (the baseline cohort)
and many times with one service made unavailable (its intervention cohort). For each cohort:

    success rate = successful eligible runs / eligible runs                       (_statistics)
    effect Δ     = baseline success rate − intervention success rate              (classify_cohort_effect)

A large Δ means the workflow fails without the service, so the service is needed; Δ near zero means the
workflow succeeds without it. The thresholds are recorded with every assessment (AssessmentPolicy), and the
same rule is used for services, for edges and for threshold-sensitivity analysis, so the labels are
comparable across all of them.
"""

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
    """Count one cohort's runs and its success rate, using only runs whose outcome is certain.

    A run is eligible when its own trace was matched exactly (no other run shares its execution or trace ID)
    and the outcome is SUCCEEDED or FAILED. Indeterminate runs (trace missing, incomplete or ambiguous) are
    excluded and listed, never counted as failures: missing telemetry must not look like a broken workflow.
    """
    outcomes = list(records)
    # Count how often each execution and each trace occurs: a run or trace that appears twice cannot be
    # attributed to one run, so it is not used (it would count the same evidence twice).
    execution_counts = Counter(item.registration.execution_id for item in outcomes)
    trace_counts = Counter(trace_id for item in outcomes for trace_id in item.trace_ids)
    # Eligible = the run's own trace was matched (not a neighbour's), it is unique, and the outcome is certain.
    # INDETERMINATE runs (trace missing or incomplete) fall out here: they are neither successes nor failures.
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
    # n = eligible runs; s = successful eligible runs; f = failed eligible runs (s + f = n).
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
            # success rate = s / n   (undefined with no eligible run, so it can never be mistaken for 0)
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
    """How much usable evidence a label rests on, between 0 and 1 (not a statistical confidence).

        strength = (eligible runs / recorded runs) × min(1, smaller cohort / minimum runs per cohort)

    The first factor drops when runs had to be excluded; the second drops when a cohort is below the minimum.
    """
    total = baseline.total_records + intervention.total_records
    eligible = baseline.eligible_records + intervention.eligible_records
    # correlation quality = eligible runs / recorded runs (1.0 when no run had to be excluded)
    correlation_quality = eligible / total if total else 0.0
    # sample coverage = smaller cohort / required runs, capped at 1 (1.0 once both cohorts are large enough)
    sample_coverage = min(
        1.0,
        min(baseline.eligible_records, intervention.eligible_records)
        / policy.min_samples_per_cohort,
    )
    # strength = correlation quality × sample coverage, e.g. (10/10) × min(1, 5/3) = 1.0
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

    With Δ = baseline success rate − intervention success rate and the policy's thresholds (defaults shown):

        Δ ≥ 0.5 and intervention success rate ≤ 0.2  → MANDATORY   the workflow fails without the service
        Δ ≥ 0.5 but intervention success rate > 0.2  → unresolved  large drop, yet runs still succeed: contradictory
        |Δ| ≤ 0.1                                    → OPTIONAL    no material change without the service
        0.1 < Δ < 0.5                                → SUPPORTING  partial degradation without the service
        Δ < −0.1                                     → unresolved  success improved: something else changed

    Example: baseline 5/5 = 1.0, outage 0/5 = 0.0 → Δ = 1.0 → MANDATORY; 5/5 and 5/5 → Δ = 0 → OPTIONAL.
    Callers still enforce intervention verification, sample and baseline gates.
    """
    if baseline.success_rate is None or intervention.success_rate is None:
        return ValidatedDependencyType.INSUFFICIENT_EVIDENCE, None
    # Δ (effect) = baseline success rate − outage success rate.
    # Δ = 1.0: every normal run succeeded and every outage run failed. Δ = 0: no difference.
    effect = round(baseline.success_rate - intervention.success_rate, 4)
    # Rule 1: Δ ≥ mandatory threshold (0.5) is a failure effect. It is MANDATORY only if runs without the service
    # also mostly fail (success rate ≤ 0.2); a big drop while runs still succeed is contradictory → unresolved.
    if effect >= policy.mandatory_effect_threshold:
        label = (
            ValidatedDependencyType.MANDATORY
            if intervention.success_rate <= policy.max_mandatory_success_rate
            else ValidatedDependencyType.INSUFFICIENT_EVIDENCE
        )
    # Rule 2: Δ < −0.1 means success improved during the outage; that points to another change → unresolved.
    elif effect < -policy.optional_effect_tolerance:
        label = ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    # Rule 3: |Δ| ≤ 0.1 (tolerance): no material change without the service → OPTIONAL.
    elif effect <= policy.optional_effect_tolerance:
        label = ValidatedDependencyType.OPTIONAL
    # Rule 4: 0.1 < Δ < 0.5: partial degradation → SUPPORTING.
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
    """Classify one candidate without hiding weak or contradictory evidence.

    Before the decision rule is applied, every gate must pass; any failure leaves the service
    INSUFFICIENT_EVIDENCE with the reason recorded:
      - the two cohorts share no run or trace (otherwise they are not independent);
      - the outage was confirmed (for automatic tests, verified through the Kubernetes API);
      - each cohort has at least ``min_samples_per_cohort`` eligible runs;
      - the baseline success rate is at least ``min_baseline_success_rate`` (an unstable workflow cannot show
        what one missing service changes).
    """
    baseline, baseline_excluded = _statistics(baseline_records, baseline_allowed_execution_ids)
    intervention_stats, intervention_excluded = _statistics(intervention_records, intervention_allowed_execution_ids)
    excluded = baseline_excluded + intervention_excluded
    reasons: list[str] = []
    # Gate 1 (independence): the normal and outage cohorts must not share a run or a trace.
    if _cohorts_overlap(baseline, intervention_stats):
        reasons.append("Baseline and intervention reuse the same execution or trace evidence")

    # Gate 2 (confirmed outage): an outage that was not confirmed cannot explain any difference.
    if intervention is None:
        reasons.append("No intervention cohort was registered for this candidate service")
    elif not intervention.intervention_confirmed:
        reasons.append("The intervention was not independently confirmed")
    # Gate 3 (sample size): each cohort needs at least the minimum number of eligible runs.
    if baseline.eligible_records < policy.min_samples_per_cohort:
        reasons.append("Baseline cohort has fewer eligible samples than required")
    if intervention_stats.eligible_records < policy.min_samples_per_cohort:
        reasons.append("Intervention cohort has fewer eligible samples than required")
    # Gate 4 (stable baseline): the normal success rate must be at least 0.8, otherwise failures happen anyway.
    if (
        baseline.success_rate is not None
        and baseline.success_rate < policy.min_baseline_success_rate
    ):
        reasons.append("Baseline success rate is below the configured stability gate")

    # Any failed gate keeps the label INSUFFICIENT_EVIDENCE; the decision rule below only applies if all pass.
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
    # Same four gates and decision rule as for services, applied to one directed call.
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
