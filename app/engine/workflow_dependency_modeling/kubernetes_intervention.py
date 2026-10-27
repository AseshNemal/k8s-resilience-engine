"""Backend-verified service outages on a Kubernetes TEST namespace, for a workflow experiment campaign.

The Kubernetes counterpart of ``local_boutique_intervention`` (which stops Docker Compose containers). For one candidate
service at a time it scales the service's Deployment to zero, waits until the API server shows no pod left, records the
outage with that evidence, runs the campaign's workflow trials, then puts the replica count back and records recovery
only after the Deployment is ready again. Each trial carries the same backend-verified outage marker as the Compose
runner, so the assessment can tell verified outages from operator-recorded ones.

Where it may act is decided by the backend, never by a request: the namespace is ``WORKFLOW_OUTAGE_NAMESPACE``, writes
need ``RESILIENCE_ALLOW_CLUSTER_WRITES=1``, and the namespace itself must carry the opt-in label (see
``app.integrations.kubernetes.workflow_outage``). The service name must be a candidate of the campaign and the name of a
Deployment in that namespace. Production analyses cannot reach here: campaigns are never created from them.

A cohort can take minutes, longer than one HTTP request through the Docker Desktop bridge may take, so it runs on a
background thread and the page polls ``kubernetes_outage_status``. The only state that matters after a crash is on the
Deployment (its recovery annotation); ``recover_kubernetes_outages`` puts it back.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
import threading
import time
from typing import Any, Callable

from app.integrations.kubernetes.workflow_outage import (
    DeploymentState,
    KubernetesDeploymentScaler,
    OutageClusterError,
    OutageNotPermittedError,
)

from .campaign_execution_service import (
    baseline_readiness,
    intervention_status,
    record_intervention_event,
    run_campaign_intervention_trial,
)
from .experiment_campaign_service import get_experiment_campaign

NAMESPACE_SETTING = "WORKFLOW_OUTAGE_NAMESPACE"
WRITES_SETTING = "RESILIENCE_ALLOW_CLUSTER_WRITES"
# Seconds; configurable on the backend so a slower cluster does not need a code change.
DOWN_TIMEOUT_SETTING = "WORKFLOW_OUTAGE_DOWN_TIMEOUT_SECONDS"
RESTORE_TIMEOUT_SETTING = "WORKFLOW_OUTAGE_RESTORE_TIMEOUT_SECONDS"
_DEFAULT_DOWN_TIMEOUT = 120.0
_DEFAULT_RESTORE_TIMEOUT = 300.0
_POLL_SECONDS = 2.0

_job_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def _seconds(setting: str, default: float) -> float:
    raw = (os.environ.get(setting) or "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError as err:
        raise ValueError(f"{setting} must be a number of seconds") from err
    if not 1 <= value <= 3600:
        raise ValueError(f"{setting} must be between 1 and 3600 seconds")
    return value


def _scaler() -> KubernetesDeploymentScaler:
    if os.environ.get(WRITES_SETTING) != "1":
        raise ValueError("Cluster writes are not enabled on this backend; outage tests cannot run")
    namespace = (os.environ.get(NAMESPACE_SETTING) or "").strip()
    if not namespace:
        raise ValueError(f"No outage namespace is configured ({NAMESPACE_SETTING})")
    return KubernetesDeploymentScaler(namespace)


def outage_scaler() -> KubernetesDeploymentScaler:
    """The scaler for the backend's configured, opted-in test namespace (refuses if writes are not armed)."""
    return _scaler()


def _wait(scaler: KubernetesDeploymentScaler, service: str, condition: Callable[[DeploymentState], bool],
          timeout: float, sleeper: Callable[[float], None], clock: Callable[[], float]) -> DeploymentState:
    deadline = clock() + timeout
    state = scaler.inspect(service)
    while not condition(state) and clock() < deadline:
        sleeper(_POLL_SECONDS)
        state = scaler.inspect(service)
    return state


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _service_progress(campaign_id: str, service: str) -> dict[str, Any]:
    return next(item for item in intervention_status(campaign_id)["services"] if item["targetService"] == service)


def _check_cohort_allowed(campaign_id: str, service: str) -> int:
    """The number of trials still needed for ``service``; raises when the cohort may not start."""
    campaign = get_experiment_campaign(campaign_id)
    if service not in campaign.candidate_services:
        raise ValueError("Target is not a candidate service of this campaign")
    if not baseline_readiness(campaign)["ready"]:
        raise ValueError("Collect a passing baseline before service outage tests")
    progress = intervention_status(campaign_id)
    if any(item["state"] == "active" for item in progress["services"]):
        raise ValueError("Another service outage is still recorded as active; recover it first")
    target = next(item for item in progress["services"] if item["targetService"] == service)
    if target["evidenceReady"]:
        raise ValueError(f"{service} already has a complete outage cohort")
    remaining = target["requiredTrials"] - target["eligibleTrials"]
    if remaining <= 0 or target["trialCount"] >= target["requiredTrials"] * 3:
        raise ValueError(f"Trial attempt limit reached for {service}; inspect its excluded runs")
    return remaining


def run_kubernetes_intervention_cohort(
    campaign_id: str,
    target_service: str,
    application_url: str,
    *,
    actor: str,
    scaler: KubernetesDeploymentScaler | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Take one service down (verified), collect its trials, always attempt verified recovery."""
    _check_cohort_allowed(campaign_id, target_service)
    cluster = scaler or _scaler()
    down_timeout = _seconds(DOWN_TIMEOUT_SETTING, _DEFAULT_DOWN_TIMEOUT)
    restore_timeout = _seconds(RESTORE_TIMEOUT_SETTING, _DEFAULT_RESTORE_TIMEOUT)
    cluster.require_opt_in()
    leftover = cluster.interrupted()
    if leftover:
        raise ValueError("An earlier outage was not recovered (" + ", ".join(leftover) + "); recover it first")
    before = cluster.inspect(target_service)
    if not before.restored:
        raise ValueError(f"{target_service} is not fully running before its outage test ({before.evidence()})")

    outcomes: list[dict[str, Any]] = []
    cluster.take_down(before)
    try:
        down = _wait(cluster, target_service, lambda s: s.down, down_timeout, sleeper, clock)
        # Verified outage: 0 replicas requested, 0 ready and no pod left (terminating pods still count as up).
        if not down.down:
            raise OutageClusterError(f"{target_service} did not go down within {down_timeout:.0f} s ({down.evidence()})")
        record_intervention_event(campaign_id, {
            "targetService": target_service, "eventType": "activated", "operatorConfirmed": True,
            "evidence": down.evidence(), "occurredAt": _now(),
        }, actor=actor)
        # Run until the cohort has enough usable runs; excluded runs (incomplete traces) are replaced, within the
        # same attempt limit the manual path has (three times the required count).
        while True:
            progress = _service_progress(campaign_id, target_service)
            # Stop when the cohort has enough usable runs, or after 3 × required attempts (excluded runs are replaced).
            if (progress["eligibleTrials"] >= progress["requiredTrials"]
                    or progress["trialCount"] >= progress["requiredTrials"] * 3):
                break
            # The missing server span of the stopped service is accepted only while the API still shows it down.
            current = cluster.inspect(target_service)
            # Re-verified before every run: a run only counts while the API still shows the service down.
            if not current.down:
                raise OutageClusterError(f"{target_service} came back during its outage test ({current.evidence()})")
            outcome = run_campaign_intervention_trial(
                campaign_id, target_service, application_url, actor=actor,
                verified_unavailable_service=target_service,
            )
            outcomes.append(outcome.to_mapping())
    finally:
        _restore(campaign_id, target_service, cluster, restore_timeout, actor, sleeper, clock)
    return {"targetService": target_service, "outcomes": outcomes, "interventionStatus": intervention_status(campaign_id)}


def _restore(campaign_id: str, service: str, cluster: KubernetesDeploymentScaler, timeout: float, actor: str,
             sleeper: Callable[[float], None], clock: Callable[[], float]) -> None:
    """Put the recorded replica count back, and record recovery only once the Deployment is verified ready."""
    state = cluster.inspect(service)
    if state.original_replicas is not None:
        cluster.restore(state)
    restored = _wait(cluster, service, lambda s: s.restored, timeout, sleeper, clock)
    # Recovery is recorded only once the Deployment is ready again with its original replica count.
    if not restored.restored:
        raise OutageClusterError(
            f"{service} was scaled back but is not ready after {timeout:.0f} s ({restored.evidence()}); "
            "its outage stays recorded as active until it is"
        )
    if _service_progress(campaign_id, service)["state"] == "active":
        record_intervention_event(campaign_id, {
            "targetService": service, "eventType": "restored", "operatorConfirmed": True,
            "evidence": restored.evidence(), "occurredAt": _now(),
        }, actor=actor)


def recover_kubernetes_outages(
    campaign_id: str,
    *,
    actor: str,
    scaler: KubernetesDeploymentScaler | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Restore every Deployment of the namespace that still carries an outage record, and close this campaign's
    active outage once its Deployment is verified ready. Never takes anything down."""
    if cluster_job_running():
        raise ValueError("An experiment is running on the test cluster; its outage is intentional. Recover only after it finishes or fails.")
    cluster = scaler or _scaler()
    restore_timeout = _seconds(RESTORE_TIMEOUT_SETTING, _DEFAULT_RESTORE_TIMEOUT)
    services = set(cluster.interrupted())
    services.update(item["targetService"] for item in intervention_status(campaign_id)["services"] if item["state"] == "active")
    for service in sorted(services):
        _restore(campaign_id, service, cluster, restore_timeout, actor, sleeper, clock)
    return {"recovered": sorted(services), "interventionStatus": intervention_status(campaign_id)}


def take_down_verified(cluster: KubernetesDeploymentScaler, services: list[str], *,
                       sleeper: Callable[[float], None] = time.sleep,
                       clock: Callable[[], float] = time.monotonic) -> list[str]:
    """Scale every service to zero and wait until the API shows each down; returns the evidence lines.

    On any failure the services already touched are left with their recovery annotation; callers restore in a
    ``finally`` with ``restore_verified``.
    """
    timeout = _seconds(DOWN_TIMEOUT_SETTING, _DEFAULT_DOWN_TIMEOUT)
    for service in services:
        state = cluster.inspect(service)
        if not state.restored:
            raise ValueError(f"{service} is not fully running before the test ({state.evidence()})")
        cluster.take_down(state)
    evidence = []
    for service in services:
        state = _wait(cluster, service, lambda s: s.down, timeout, sleeper, clock)
        if not state.down:
            raise OutageClusterError(f"{service} did not go down within {timeout:.0f} s ({state.evidence()})")
        evidence.append(state.evidence())
    return evidence


def restore_verified(cluster: KubernetesDeploymentScaler, services: list[str], *,
                     sleeper: Callable[[float], None] = time.sleep,
                     clock: Callable[[], float] = time.monotonic) -> list[str]:
    """Put back each recorded replica count and wait until each Deployment is ready; returns the evidence lines."""
    timeout = _seconds(RESTORE_TIMEOUT_SETTING, _DEFAULT_RESTORE_TIMEOUT)
    for service in services:
        state = cluster.inspect(service)
        if state.original_replicas is not None:
            cluster.restore(state)
    evidence, not_ready = [], []
    for service in services:
        state = _wait(cluster, service, lambda s: s.restored, timeout, sleeper, clock)
        (evidence if state.restored else not_ready).append(state.evidence())
    if not_ready:
        raise OutageClusterError("Scaled back but not ready yet: " + "; ".join(not_ready))
    return evidence


def start_cluster_job(key: str, info: dict[str, Any], work: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Run ``work(job)`` on a background thread; only one cluster-changing job runs at a time in this backend."""
    with _job_lock:
        if any(job["state"] == "running" for job in _jobs.values()):
            raise ValueError("A service outage test is already running; wait for it to finish")
        job = {**info, "state": "running", "error": None, "startedAt": _now(), "finishedAt": None}
        _jobs[key] = job

        def run() -> None:
            try:
                work(job)
                job["state"] = "completed"
            except Exception as error:  # reported to the page; Deployment annotations drive recovery
                job.update(state="failed", error=str(error) or type(error).__name__)
            finally:
                job["finishedAt"] = _now()

        threading.Thread(target=run, name=f"workflow-cluster-job-{key}", daemon=True).start()
        return dict(job)


def cluster_job_running() -> bool:
    return any(job["state"] == "running" for job in _jobs.values())


def cluster_job(key: str) -> dict[str, Any] | None:
    job = _jobs.get(key)
    return dict(job) if job else None


def _remaining_services(campaign_id: str) -> list[str]:
    return [item["targetService"] for item in intervention_status(campaign_id)["services"] if not item["evidenceReady"]]


def _run_job(campaign_id: str, services: list[str], application_url: str, actor: str) -> None:
    job = _jobs[campaign_id]
    try:
        incomplete = []
        for service in services:
            job.update(currentService=service, phase="running")
            run_kubernetes_intervention_cohort(campaign_id, service, application_url, actor=actor)
            job["completedServices"].append(service)
            # A cohort can end at its attempt limit with too few usable runs; say so instead of "completed".
            if not _service_progress(campaign_id, service).get("evidenceReady"):
                incomplete.append(service)
        job.update(state="completed", currentService=None, phase=None, incompleteServices=incomplete,
                   error=("Too few usable runs for: " + ", ".join(incomplete)) if incomplete else None)
    except Exception as error:  # reported to the page; the Deployment's own record drives recovery
        job.update(state="failed", error=str(error) or type(error).__name__, phase=None)
    finally:
        job["finishedAt"] = _now()


def start_kubernetes_outage_job(
    campaign_id: str,
    application_url: str,
    *,
    actor: str,
    target_service: str | None = None,
) -> dict[str, Any]:
    """Start outage cohorts for one service, or every service still without a complete cohort, in the background."""
    if not isinstance(application_url, str) or not application_url.strip():
        raise ValueError("applicationUrl is required")
    services = [target_service] if target_service else _remaining_services(campaign_id)
    if not services:
        raise ValueError("Every candidate service already has a complete outage cohort")
    # Fail fast on the first service's gates and on the cluster opt-in, before anything runs in the background.
    _check_cohort_allowed(campaign_id, services[0])
    cluster = _scaler()
    cluster.require_opt_in()
    with _job_lock:
        if any(job["state"] == "running" for job in _jobs.values()):
            raise ValueError("A service outage test is already running; wait for it to finish")
        _jobs[campaign_id] = {
            "campaignId": campaign_id, "state": "running", "services": list(services), "completedServices": [],
            "currentService": None, "phase": "starting", "error": None, "startedAt": _now(), "finishedAt": None,
            "namespace": cluster.namespace,
        }
        thread = threading.Thread(target=_run_job, args=(campaign_id, services, application_url.strip(), actor),
                                  name=f"workflow-outage-{campaign_id}", daemon=True)
        thread.start()
        return dict(_jobs[campaign_id])


def kubernetes_outage_status(campaign_id: str) -> dict[str, Any]:
    """Whether outage tests can run here, the latest job of this campaign, and any outage left unrecovered."""
    get_experiment_campaign(campaign_id)
    job = _jobs.get(campaign_id)
    status: dict[str, Any] = {"available": False, "reason": None, "namespace": None, "interrupted": [],
                              "job": dict(job) if job else None, "busy": False}
    if cluster_job_running():
        # A running test has a service down on purpose; it is not an outage left behind and must not be restored.
        status.update(busy=True, reason="An experiment is running on the test cluster; outage controls return when it finishes.")
        return status
    try:
        cluster = _scaler()
        status["namespace"] = cluster.namespace
        cluster.require_opt_in()
        status["interrupted"] = cluster.interrupted()
        status["available"] = True
    except (ValueError, OutageNotPermittedError, OutageClusterError) as error:
        status["reason"] = str(error)
    return status


__all__ = [
    "OutageClusterError",
    "kubernetes_outage_status",
    "recover_kubernetes_outages",
    "run_kubernetes_intervention_cohort",
    "start_kubernetes_outage_job",
]
