"""Held-out validation of dependency labels: predict outcomes of untested outages, then observe them.

The labels of an assessment come from single-service outages. They are checked here against scenarios the
classifier never saw, pairs of services unavailable at the same time, chosen at random with a recorded seed:

1. Plan. Both predictions are frozen and stored before anything runs:
   - this method: the workflow fails if a mandatory service is down; it survives if every service down is
     optional; otherwise (supporting or unresolved services involved) the prediction is "uncertain";
   - the trace-only baseline (B1): every observed service is a dependency, so it predicts failure for every pair.
2. Execute. Both services are scaled to zero and verified down, the campaign's own workflow runs as many times as
   its baseline, and both are restored and verified ready. The runs are stored in their own batch; they never join
   the single-service cohorts the labels came from.
3. Observe. The scenario's success rate is compared with the campaign baseline by the same rule and thresholds as
   the labels: an effect at the mandatory threshold is "fail", a change within the optional tolerance is
   "survive", anything between is "degraded", and failed evidence gates leave it "unresolved".
4. Score. Failure is the positive class. Scenarios observed as "degraded" or "unresolved" are reported and left out
   of the confusion matrices; "uncertain" predictions are left out of the method's matrix and counted against its
   coverage, and are also scored once as wrong so the comparison with B1 cannot be improved by abstaining.

Only service-level outages, in the opted-in test namespace: node failures belong to Components 3 and 4.
"""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import combinations
import random
import secrets
from typing import Any, Callable
from uuid import uuid4

from .analysis_session_service import get_workflow_analysis
from .campaign_assessment_service import get_experiment_campaign_assessment
from .campaign_execution_service import (
    _eligible_campaign_outcome,
    _record_campaign_trial,
    baseline_readiness,
    intervention_status,
)
from .dependency_assessment_models import AssessmentPolicy, ValidatedDependencyType
from .dependency_assessor import _statistics, classify_cohort_effect
from .execution_repository import ExecutionRepository
from .execution_service import list_executions
from .experiment_campaign_service import get_experiment_campaign
from .held_out_validation_repository import (
    HeldOutValidationRepository,
    SqlAlchemyHeldOutValidationRepository,
)
from .kubernetes_intervention import (
    cluster_job,
    outage_scaler,
    restore_verified,
    start_cluster_job,
    take_down_verified,
)
from .label_statistics import confidence_level, wilson_interval

_MAX_SCENARIOS = 50
_default_repository: HeldOutValidationRepository | None = None


def configure_held_out_repository(repository: HeldOutValidationRepository) -> None:
    global _default_repository
    _default_repository = repository


def _repository(repository: HeldOutValidationRepository | None) -> HeldOutValidationRepository:
    if repository is not None:
        return repository
    global _default_repository
    if _default_repository is None:
        from app.core.config import get_settings
        from app.core.database import get_engine

        _default_repository = SqlAlchemyHeldOutValidationRepository(get_engine(get_settings().database_url))
    return _default_repository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def predict(services: tuple[str, ...], labels: dict[str, str]) -> dict[str, Any]:
    """Both predictions for one scenario, with the labels they were derived from."""
    # The labels the prediction is derived from; a service never assessed cannot support a prediction.
    basis = {name: labels.get(name, "not_assessed") for name in services}
    # Prediction rule (this method): one mandatory service down is enough for the workflow to fail ...
    if any(label == ValidatedDependencyType.MANDATORY.value for label in basis.values()):
        method, rule = "fail", "a mandatory service is unavailable"
    # ... it survives only if every service down is optional; any supporting or unresolved one → no prediction.
    elif all(label == ValidatedDependencyType.OPTIONAL.value for label in basis.values()):
        method, rule = "survive", "every unavailable service is optional"
    else:
        method, rule = "uncertain", "supporting or unresolved services are unavailable and no mandatory one"
    return {"method": method, "methodRule": rule, "traceOnly": "fail",
            "traceOnlyRule": "every observed service is a dependency", "labels": basis}


def plan_held_out_run(
    campaign_id: str,
    *,
    actor: str,
    scenario_count: int,
    seed: int | None = None,
    sampling: str = "stratified",
    repository: HeldOutValidationRepository | None = None,
) -> dict[str, Any]:
    """Sample pairs of assessed services and freeze both predictions for each before any outage runs."""
    if isinstance(scenario_count, bool) or not isinstance(scenario_count, int) or not 1 <= scenario_count <= _MAX_SCENARIOS:
        raise ValueError(f"scenarioCount must be an integer between 1 and {_MAX_SCENARIOS}")
    if sampling not in ("stratified", "uniform"):
        raise ValueError('sampling must be "stratified" or "uniform"')
    assessment = get_experiment_campaign_assessment(campaign_id)
    labels = {item.service_name: item.classification.value for item in assessment.services}
    # All unordered pairs of assessed services: k services → k(k−1)/2 pairs (9 → 36). None was ever tested
    # together, so their outcomes are new evidence the labels were not fitted to.
    pairs = list(combinations(sorted(labels), 2))
    if not pairs:
        raise ValueError("Held-out pairs need at least two assessed services")
    seed = secrets.randbelow(2**31) if seed is None else seed
    # Strata = what this method predicts for the pair. Stratifying by prediction (never by outcome) keeps the
    # draw unbiased while making sure the rare pairs, where it disagrees with the trace baseline, are tested.
    groups = {name: [] for name in ("fail", "survive", "uncertain")}
    for pair in pairs:
        groups[predict(pair, labels)["method"]].append(pair)
    count = min(scenario_count, len(pairs))
    # One seeded generator: the same seed and labels always give the same pairs, so the draw is reproducible.
    rng = random.Random(seed)
    if sampling == "uniform":
        chosen = sorted(rng.sample(pairs, count))
        description = f"{count} of {len(pairs)} service pairs drawn uniformly with random.Random({seed}).sample"
    else:
        allocations = dict.fromkeys(groups, 0)
        # Capped round-robin balances non-empty strata and redistributes exhausted groups' slots.
        # Population size, then name, resolves any partial round deterministically.
        order = sorted(groups, key=lambda name: (-len(groups[name]), name))
        remaining = count
        while remaining:
            for name in order:
                if remaining and allocations[name] < len(groups[name]):
                    allocations[name] += 1
                    remaining -= 1
        chosen = sorted(pair for name in sorted(groups)
                        for pair in rng.sample(sorted(groups[name]), allocations[name]))
        description = (
            f"{count} of {len(pairs)} service pairs drawn by prediction stratum; slots allocated evenly in capped "
            f"rounds (largest population first, ties by name), redistributing exhausted groups' slots; "
            f"sorted pairs sampled within each group in name order using one random.Random({seed}).sample generator"
        )
    group_counts = {name: {"available": len(group),
                           "drawn": sum(predict(pair, labels)["method"] == name for pair in chosen)}
                    for name, group in groups.items()}
    description += "; " + ", ".join(f"{name}: {counts['drawn']} drawn of {counts['available']}"
                                     for name, counts in group_counts.items())
    run_id = f"heldout:{uuid4().hex[:16]}"
    plan = {
        "kind": "plan", "runId": run_id, "campaignId": campaign_id, "assessmentId": assessment.assessment_id,
        "seed": seed, "pairsAvailable": len(pairs), "policy": assessment.policy.to_mapping(),
        "scenarios": [{"scenarioId": f"s{index:02d}", "services": list(services), "prediction": predict(services, labels)}
                      for index, (services) in enumerate(chosen, start=1)],
        "samplingMethod": sampling, "samplingGroups": group_counts, "sampling": description,
        "createdAt": _now(), "createdBy": actor,
    }
    _repository(repository).create(f"{campaign_id}|{run_id}|plan", plan)
    return plan


def _records(campaign_id: str, run_id: str | None, repository: HeldOutValidationRepository | None) -> list[dict[str, Any]]:
    return _repository(repository).list(f"{campaign_id}|{run_id or ''}")


def _observe(baseline_records: list, scenario_records: list, policy: AssessmentPolicy) -> dict[str, Any]:
    """What actually happened in one held-out scenario, judged exactly like a single-service cohort.

    Δ = baseline success rate − scenario success rate, through classify_cohort_effect:
        MANDATORY → "fail"   (the workflow fails with these services down)
        OPTIONAL  → "survive" (no material change)
        SUPPORTING → "degraded" (partial drop; reported, not scored)
        otherwise or a failed gate → "unresolved" (reported, not scored)
    """
    # The same baseline runs the assessment counted, so a scenario is compared like a single-service cohort.
    baseline, _ = _statistics([item for item in baseline_records if _eligible_campaign_outcome(item)])
    scenario, excluded = _statistics(scenario_records)
    # Observed Δ = baseline success rate − scenario success rate, judged by the same rule as the labels
    label, effect = classify_cohort_effect(baseline, scenario, policy)
    # Too few usable runs, or an unstable baseline, means the outcome is not known: unresolved, not scored.
    gated = (baseline.eligible_records < policy.min_samples_per_cohort
             or scenario.eligible_records < policy.min_samples_per_cohort
             or (baseline.success_rate or 0.0) < policy.min_baseline_success_rate)
    outcome = "unresolved" if gated else {
        ValidatedDependencyType.MANDATORY: "fail", ValidatedDependencyType.OPTIONAL: "survive",
        ValidatedDependencyType.SUPPORTING: "degraded",
    }.get(label, "unresolved")
    return {"outcome": outcome, "effect": effect, "baseline": baseline.to_mapping(), "scenario": scenario.to_mapping(),
            "excludedExecutionIds": list(excluded),
            "rule": "same decision rule and thresholds as the labels, applied to this scenario's runs"}


def run_held_out_scenario(
    campaign_id: str, run_id: str, scenario: dict[str, Any], application_url: str, *, actor: str,
    repository: HeldOutValidationRepository | None = None,
    execution_repository: ExecutionRepository | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Take the scenario's services down (verified), run the workflow, restore, and store the observation."""
    campaign = get_experiment_campaign(campaign_id)
    if not baseline_readiness(campaign, execution_repository)["ready"]:
        raise ValueError("The campaign baseline is not ready")
    if any(item["state"] == "active" for item in intervention_status(campaign_id)["services"]):
        raise ValueError("A single-service outage is still recorded as active; recover it first")
    analysis = get_workflow_analysis(campaign.analysis_session_id)
    services = list(scenario["services"])
    cluster = outage_scaler()
    cluster.require_opt_in()
    if cluster.interrupted():
        raise ValueError("An earlier outage was not recovered (" + ", ".join(cluster.interrupted()) + "); recover it first")
    batch_id = f"{campaign_id}:{run_id}:{scenario['scenarioId']}"
    started = _now()
    try:
        down = take_down_verified(cluster, services)
        # Runs until the scenario has as many usable runs as the baseline requires; runs excluded for incomplete
        # evidence are replaced within the same attempt limit as service cohorts (three times the requirement).
        required = campaign.baseline.required_trials
        while True:
            recorded = list_executions(batch_id=batch_id, repository=execution_repository)
            usable, _ = _statistics(recorded)
            if usable.eligible_records >= required or len(recorded) >= required * 3:
                break
            if not all(cluster.inspect(name).down for name in services):
                raise ValueError("A scenario service came back during its runs")
            _record_campaign_trial(campaign, analysis, batch_id, application_url, actor, execution_repository,
                                   None, None, sleeper, None, tuple(sorted(services)))
    finally:
        restored = restore_verified(cluster, services)
    observation = _observe(
        list_executions(batch_id=campaign.baseline.batch_id, repository=execution_repository),
        list_executions(batch_id=batch_id, repository=execution_repository),
        campaign_policy(campaign_id),
    )
    result = {"kind": "result", "runId": run_id, "scenarioId": scenario["scenarioId"], "services": services,
              "batchId": batch_id, "startedAt": started, "finishedAt": _now(),
              "outageEvidence": down, "recoveryEvidence": restored, "observation": observation, "recordedBy": actor}
    _repository(repository).create(f"{campaign_id}|{run_id}|result|{scenario['scenarioId']}", result)
    return result


def campaign_policy(campaign_id: str) -> AssessmentPolicy:
    return get_experiment_campaign_assessment(campaign_id).policy


def start_held_out_run(campaign_id: str, run_id: str, application_url: str, *, actor: str) -> dict[str, Any]:
    """Run every scenario of the plan that has no result yet, one at a time, in the background."""
    if not isinstance(application_url, str) or not application_url.strip():
        raise ValueError("applicationUrl is required")
    plan, results = _plan_and_results(campaign_id, run_id, None)
    pending = [item for item in plan["scenarios"] if item["scenarioId"] not in results]
    if not pending:
        raise ValueError("Every scenario of this run already has a result")
    outage_scaler().require_opt_in()

    def work(job: dict[str, Any]) -> None:
        for scenario in pending:
            job["currentScenario"] = scenario["scenarioId"]
            run_held_out_scenario(campaign_id, run_id, scenario, application_url.strip(), actor=actor)
            job["completedScenarios"].append(scenario["scenarioId"])
        job["currentScenario"] = None

    return start_cluster_job(f"{campaign_id}|{run_id}", {
        "campaignId": campaign_id, "runId": run_id, "scenarios": [item["scenarioId"] for item in pending],
        "completedScenarios": [], "currentScenario": pending[0]["scenarioId"],
    }, work)


def _plan_and_results(campaign_id: str, run_id: str, repository: HeldOutValidationRepository | None):
    records = _records(campaign_id, run_id, repository)
    plans = [item for item in records if item.get("kind") == "plan" and item.get("runId") == run_id]
    if len(plans) != 1:
        raise ValueError("Held-out run was not found for this campaign")
    results = {item["scenarioId"]: item for item in records if item.get("kind") == "result" and item.get("runId") == run_id}
    return plans[0], results


def _metrics(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """Confusion counts and scores with "fail" as the positive class; pairs are (predicted, observed)."""
    # Confusion matrix with failure as the positive class (p = predicted, o = observed):
    # TP predicted fail and failed · FP predicted fail but survived · FN predicted survive but failed ·
    # TN predicted survive and survived.
    tp = sum(p == "fail" and o == "fail" for p, o in pairs)
    fp = sum(p == "fail" and o == "survive" for p, o in pairs)
    fn = sum(p != "fail" and o == "fail" for p, o in pairs)
    tn = sum(p != "fail" and o == "survive" for p, o in pairs)
    n = tp + fp + fn + tn
    # Precision = TP / (TP + FP): of the predicted failures, the share that really failed (penalises
    # calling an optional service mandatory). Undefined, not 0, when nothing was predicted to fail.
    precision = tp / (tp + fp) if tp + fp else None
    # Recall = TP / (TP + FN): of the real failures, the share that was predicted (penalises missing a
    # mandatory service).
    recall = tp / (tp + fn) if tp + fn else None
    if precision is None or recall is None:
        f1 = None
    else:
        # F1 = 2PR / (P + R): harmonic mean of precision and recall, low if either is low.
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    # Accuracy = correct predictions / scored scenarios
    accuracy = (tp + tn) / n if n else None
    # Wilson interval of the accuracy: with few scenarios even 10/10 correct only shows accuracy ≥ ~0.72.
    interval = wilson_interval(tp + tn, n, confidence_level()) if n else None
    return {"truePositives": tp, "falsePositives": fp, "falseNegatives": fn, "trueNegatives": tn, "scored": n,
            "precision": precision, "recall": recall, "f1": f1, "accuracy": accuracy,
            "accuracyInterval": list(interval) if interval else None,
            "formulas": "precision = TP/(TP+FP); recall = TP/(TP+FN); F1 = 2PR/(P+R); accuracy = (TP+TN)/scored"}


def score_held_out_run(campaign_id: str, run_id: str, repository: HeldOutValidationRepository | None = None) -> dict[str, Any]:
    plan, results = _plan_and_results(campaign_id, run_id, repository)
    rows = []
    for scenario in plan["scenarios"]:
        result = results.get(scenario["scenarioId"])
        rows.append({"scenarioId": scenario["scenarioId"], "services": scenario["services"],
                     "prediction": scenario["prediction"], "observation": result["observation"] if result else None})
    # Only scenarios whose outcome is clear are scored; degraded and unresolved ones are reported separately.
    decided = [row for row in rows if row["observation"] and row["observation"]["outcome"] in {"fail", "survive"}]
    # This method, scored on the pairs it made a prediction for (coverage reported alongside).
    method_pairs = [(row["prediction"]["method"], row["observation"]["outcome"]) for row in decided
                    if row["prediction"]["method"] != "uncertain"]
    # This method again, with every "no prediction" counted as a wrong prediction: abstaining cannot raise
    # its score, so its comparison with the trace baseline stays fair.
    strict_pairs = [("wrong", row["observation"]["outcome"]) if row["prediction"]["method"] == "uncertain"
                    else (row["prediction"]["method"], row["observation"]["outcome"]) for row in decided]
    # An abstention counted as wrong: predicted the opposite of what was observed.
    strict_pairs = [(("survive" if o == "fail" else "fail") if p == "wrong" else p, o) for p, o in strict_pairs]
    # Trace baseline (B1): every observed service is a dependency, so it predicts failure for every pair.
    trace_pairs = [(row["prediction"]["traceOnly"], row["observation"]["outcome"]) for row in decided]
    return {
        "runId": run_id, "campaignId": campaign_id, "assessmentId": plan["assessmentId"], "seed": plan["seed"],
        "sampling": plan["sampling"], "plannedAt": plan["createdAt"], "policy": plan["policy"],
        "samplingMethod": plan.get("samplingMethod", "uniform"),
        "samplingGroups": plan.get("samplingGroups"),
        "samplingNote": (
            "A stratified sample over-represents rare prediction groups, so its accuracy describes the predictors "
            "on these strata, not on the population of all service pairs."
            if plan.get("samplingMethod") == "stratified" else
            "Pairs were sampled uniformly from all service pairs; scores describe the observed sample."
        ),
        "scenarios": rows,
        "counts": {"planned": len(rows), "observed": sum(bool(row["observation"]) for row in rows),
                   "decided": len(decided),
                   "degraded": sum(bool(row["observation"]) and row["observation"]["outcome"] == "degraded" for row in rows),
                   "unresolved": sum(bool(row["observation"]) and row["observation"]["outcome"] == "unresolved" for row in rows),
                   "methodUncertain": sum(row["prediction"]["method"] == "uncertain" for row in decided)},
        "method": _metrics(method_pairs),
        "methodUncertainCountedWrong": _metrics(strict_pairs),
        "traceOnly": _metrics(trace_pairs),
        "methodCoverage": (len(method_pairs) / len(decided)) if decided else None,
        "job": cluster_job(f"{campaign_id}|{run_id}"),
        "positiveClass": "fail",
    }


def list_held_out_runs(campaign_id: str, repository: HeldOutValidationRepository | None = None) -> list[dict[str, Any]]:
    get_experiment_campaign(campaign_id)
    plans = [item for item in _records(campaign_id, None, repository) if item.get("kind") == "plan"]
    return [score_held_out_run(campaign_id, plan["runId"], repository)
            for plan in sorted(plans, key=lambda item: item["createdAt"])]
