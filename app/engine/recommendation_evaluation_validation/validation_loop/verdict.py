"""The pass rule: when one node failure counts as survived, when a candidate is proven, and which proven candidate wins.

Pure functions over observed readiness and the probe's own report; nothing here touches a cluster or recomputes Component 3.

Two kinds of evidence are judged here and kept apart:
* READINESS: what Kubernetes showed after the worker was stopped. Every mandatory service of the workflow must keep at least
  one Ready pod on a node other than the stopped worker. This is counted straight from the observed placement.
* PROBE: what the HTTP check of the workflow's entry point saw during the failure window (the page's status and its text).

A candidate passes an experiment only when readiness holds AND, if a probe was configured, the probe saw the workflow working
with enough samples to say so. Too few samples is not a pass: the result is INCONCLUSIVE and the candidate stops, because a
candidate with incomplete evidence is never selected.

A probe finding alone is not a prediction error. The model predicts from readiness, so "the model was wrong" is only said when
the model's prediction disagrees with the readiness-derived observation; a probe that disagrees with readiness is reported as
its own finding.
"""

from typing import Any

from app.engine.recommendation_evaluation_validation.validation_loop.models import (
    L1_READINESS,
    L2_PROBE,
    CandidateOutcome,
    CandidateStatus,
    ExperimentKey,
    ExperimentOutcome,
    ExperimentStatus,
)
from app.shared.schemas.deployment import DeploymentGraph


def mandatory_readiness(graph: DeploymentGraph, mandatory_services: list[str] | tuple[str, ...], worker: str) -> dict[str, Any]:
    """Per mandatory service: Ready pods on other nodes, Ready pods still on the stopped worker, and whether it holds.

    ``ok`` is None for a service the observation does not contain, never a guess.
    """
    by_name = {service.name: service for service in graph.services}
    services: dict[str, dict[str, Any]] = {}
    for name in sorted(mandatory_services):
        service = by_name.get(name)
        if service is None:
            services[name] = {"readyElsewhere": None, "readyOnWorker": None, "ok": None}
            continue
        elsewhere = sum(1 for pod in service.pods if pod.ready and pod.node != worker)
        on_worker = sum(1 for pod in service.pods if pod.ready and pod.node == worker)
        services[name] = {"readyElsewhere": elsewhere, "readyOnWorker": on_worker, "ok": elsewhere >= 1}
    verdicts = [entry["ok"] for entry in services.values()]
    holds: bool | None
    if any(v is False for v in verdicts):
        holds = False
    elif any(v is None for v in verdicts) or not verdicts:
        holds = None
    else:
        holds = True
    return {"worker": worker, "services": services, "holds": holds}


def _model_agrees(comparison: dict[str, Any]) -> bool | None:
    if not comparison:
        return None
    survival = comparison.get("mandatoryWorkflowSurvival") or {}
    services = (comparison.get("serviceSurvival") or {}).get("services") or []
    if "match" not in survival:
        return None
    return bool(survival["match"]) and all(entry.get("match") for entry in services)


def judge_experiment(key: ExperimentKey, readiness: dict[str, Any], probe_report: dict[str, Any] | None, *,
                     probe_required: bool, comparison: dict[str, Any] | None = None,
                     prediction_error: float | None = None, predicted: dict[str, Any] | None = None) -> ExperimentOutcome:
    """The outcome of one node failure from the readiness it left and the probe's report."""
    reasons: list[str] = []
    holds = readiness.get("holds")
    probe_survives = None if probe_report is None else probe_report.get("workflowSurvives")
    if holds is False:
        failing = [name for name, entry in readiness["services"].items() if entry["ok"] is False]
        reasons.append(f"No Ready pod outside {readiness['worker']} for: {', '.join(failing)}.")
    if probe_survives is False:
        windows = (probe_report or {}).get("windows", {}).get("failure", {})
        seen = ", ".join(sorted(windows.get("reasons") or {})) or "the check failed"
        reasons.append(f"The workflow check failed while {readiness['worker']} was down: {seen}.")

    if holds is False or probe_survives is False:
        status = ExperimentStatus.FAIL
    elif holds is None:
        status = ExperimentStatus.INCONCLUSIVE
        reasons.append("Readiness of every mandatory service could not be read from the observation.")
    elif probe_required and probe_survives is None:
        status = ExperimentStatus.INCONCLUSIVE
        reasons.append("The workflow check took too few samples in the failure window to say whether the workflow worked.")
    else:
        status = ExperimentStatus.PASS

    agrees = _model_agrees(comparison or {})
    # The probe is its own finding. It is only compared with readiness, never counted as a model error.
    probe_vs_readiness = None if (probe_survives is None or holds is None) else (probe_survives != holds)
    return ExperimentOutcome(
        key=key, status=status, reasons=reasons, evidence_level=L2_PROBE if probe_required else L1_READINESS,
        readiness=readiness, probe=probe_report, predicted=predicted or {}, comparison=comparison or {},
        prediction_error=prediction_error, model_agrees=agrees, probe_disagrees_with_readiness=probe_vs_readiness)


def judge_candidate(planned_experiments: int, outcomes: list[ExperimentOutcome]) -> tuple[CandidateStatus, str | None]:
    """PASSED only when every planned experiment ran and passed; a candidate with any gap is never PASSED."""
    candidate = [o for o in outcomes if o.key.kind == "candidate"]
    if any(o.status is ExperimentStatus.FAIL for o in candidate):
        failed = next(o for o in candidate if o.status is ExperimentStatus.FAIL)
        return CandidateStatus.FAILED, f"{failed.key.worker} (repeat {failed.key.repeat}): " + " ".join(failed.reasons)
    passed = [o for o in candidate if o.status is ExperimentStatus.PASS]
    if planned_experiments > 0 and len(passed) == planned_experiments:
        return CandidateStatus.PASSED, None
    missing = planned_experiments - len(passed)
    return CandidateStatus.INCONCLUSIVE, f"{missing} of {planned_experiments} planned experiments did not produce a pass."


def select_winner(outcomes: list[CandidateOutcome], order: dict[str, Any]) -> str | None:
    """The proven candidate with the smallest change. ``order`` maps a candidate id to a sortable key (smaller change first,
    then better rank). Only PASSED candidates are eligible: a candidate with incomplete evidence is never selected."""
    proven = [o.candidate_id for o in outcomes if o.status is CandidateStatus.PASSED and o.candidate_id in order]
    return min(proven, key=lambda cid: order[cid]) if proven else None
