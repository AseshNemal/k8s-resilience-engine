"""Repeat a reviewed experiment end to end: fresh inspection, new experiment, baseline, outage tests, assessment.

A saved experiment is immutable, so improving it (more runs per group, services that an earlier version missed) means
a new experiment. Its business inputs are not re-entered or invented: the request steps, success checks, business
outcome and importance are copied from the experiment a person already reviewed. What is new is read from evidence:
a fresh inspection of the same operation and the services every step uses. The person confirms that reuse and the
outages when starting. Approval of the result is still a separate, named human decision.

The whole chain runs as one background job in the same registry as outage tests, so it never overlaps another
cluster-changing job.
"""

from __future__ import annotations

from typing import Any

from .analysis_session_service import get_workflow_analysis, start_workflow_analysis
from .campaign_assessment_service import assess_experiment_campaign
from .campaign_execution_service import baseline_readiness, intervention_status, run_campaign_baseline_trial
from .experiment_campaign_service import create_experiment_campaign, get_experiment_campaign
from .experiment_manifest_service import create_experiment_manifest
from .kubernetes_intervention import (
    cluster_job,
    cluster_job_running,
    outage_scaler,
    run_kubernetes_intervention_cohort,
    start_cluster_job,
)

_DEFAULT_LOOKBACK = "24h"


def _job_key(campaign_id: str) -> str:
    return f"pipeline:{campaign_id}"


def _collect_baseline(job: dict[str, Any], campaign_id: str, url: str, actor: str) -> None:
    job["phase"] = "baseline"
    while True:
        readiness = baseline_readiness(get_experiment_campaign(campaign_id))
        job["baselineRuns"] = f"{readiness['eligibleTrials']}/{readiness['requiredTrials']}"
        if readiness["ready"]:
            return
        if readiness["recordedTrials"] >= readiness["requiredTrials"] * 3:
            raise ValueError("The baseline did not become stable: " + "; ".join(readiness["blockers"]))
        run_campaign_baseline_trial(campaign_id, url, actor=actor)


def _run_outages(job: dict[str, Any], campaign_id: str, url: str, actor: str) -> list[str]:
    job["phase"] = "outages"
    services = [item["targetService"] for item in intervention_status(campaign_id)["services"]]
    for index, service in enumerate(services, start=1):
        job.update(currentService=service, servicesDone=f"{index - 1}/{len(services)}")
        run_kubernetes_intervention_cohort(campaign_id, service, url, actor=actor)
    return services


def _run_pipeline(job: dict[str, Any], campaign_id: str, url: str, actor: str) -> None:
    _collect_baseline(job, campaign_id, url, actor)
    services = _run_outages(job, campaign_id, url, actor)
    incomplete = [item["targetService"] for item in intervention_status(campaign_id)["services"] if not item["evidenceReady"]]
    if incomplete:
        raise ValueError("Too few usable runs for: " + ", ".join(incomplete))
    job.update(phase="assessment", currentService=None, servicesDone=f"{len(services)}/{len(services)}")
    assess_experiment_campaign(campaign_id, actor=actor)
    job["phase"] = "done"


def repeat_experiment(
    campaign_id: str,
    *,
    actor: str,
    required_trials: int,
    application_url: str,
    lookback: str = _DEFAULT_LOOKBACK,
) -> dict[str, Any]:
    """Create a new experiment from a reviewed one and start running it; returns the new experiment and its job."""
    if not isinstance(application_url, str) or not application_url.strip():
        raise ValueError("applicationUrl is required")
    if cluster_job_running():
        raise ValueError("Another experiment or outage test is running; wait for it to finish")
    source = get_experiment_campaign(campaign_id)
    if source.execution_definition is None:
        raise ValueError("This experiment has no reviewed request steps to repeat")
    # Fail before creating anything if outages cannot run here.
    outage_scaler().require_opt_in()
    analysis = get_workflow_analysis(source.analysis_session_id)
    inspection = start_workflow_analysis({
        "environmentId": analysis.environment_id,
        "evidenceTier": analysis.evidence_tier.value,
        "traceSource": analysis.trace_source.to_mapping(),
        "entryService": analysis.entry_service,
        "operationName": analysis.operation_name,
        "lookback": lookback,
    }, actor=actor)
    campaign = create_experiment_campaign({
        "analysisSessionId": inspection.session_id,
        "successContractConfirmed": True,
        "requiredBaselineTrials": required_trials,
        "criticality": source.require_criticality(),
        "workflowName": source.workflow_name,
        "businessOutcome": source.success_contract.outcome,
        "executionDefinition": source.execution_definition.to_mapping(),
    }, actor=actor)
    create_experiment_manifest(campaign.campaign_id, {}, actor=actor)
    new_id = campaign.campaign_id
    url = application_url.strip()

    def work(job: dict[str, Any]) -> None:
        _run_pipeline(job, new_id, url, actor)

    job = start_cluster_job(_job_key(new_id), {
        "campaignId": new_id, "repeatedFrom": campaign_id, "phase": "starting", "baselineRuns": None,
        "currentService": None, "servicesDone": None,
    }, work)
    return {"campaign": campaign.to_mapping(), "job": job, "repeatedFrom": campaign_id,
            "inspection": inspection.session_id}


def pipeline_status(campaign_id: str) -> dict[str, Any]:
    get_experiment_campaign(campaign_id)
    return {"campaignId": campaign_id, "job": cluster_job(_job_key(campaign_id))}
