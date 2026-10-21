"""Explicit, local-only Docker intervention for the Online Boutique demo."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from threading import Lock
from typing import Any

from .analysis_session_service import get_workflow_analysis
from .campaign_execution_service import (
    baseline_readiness,
    intervention_status,
    record_intervention_event,
    run_campaign_intervention_trial,
)
from .experiment_campaign_service import (
    ONLINE_BOUTIQUE_CHECKOUT_PROFILE,
    get_experiment_campaign,
)


_DEMO_SERVICES = frozenset({
    "adservice", "cartservice", "checkoutservice", "currencyservice",
    "emailservice", "paymentservice", "productcatalogservice",
    "recommendationservice", "shippingservice",
})
_COMPOSE_FILE = (Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.boutique.yml").resolve()
_INSTRUMENTED_OVERRIDE = (
    Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.boutique-instrumented.yml"
).resolve()
_COHORT_LOCK = Lock()


class DockerDemoController:
    """Use only named containers proven to belong to this repository's Compose file."""

    def _command(self, *arguments: str) -> str:
        try:
            result = subprocess.run(
                ["docker", *arguments], capture_output=True, text=True,
                timeout=45, check=True,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
            raise RuntimeError(f"Local demo Docker command failed: {arguments[0]}") from err
        return result.stdout.strip()

    def inspect(self, service: str) -> dict[str, Any]:
        if service not in _DEMO_SERVICES:
            raise ValueError("Service is not an approved local Online Boutique demo target")
        container = f"boutique-{service}"
        try:
            data = json.loads(self._command("inspect", container, "--format", "{{json .}}"))
        except (TypeError, ValueError) as err:
            raise RuntimeError("Docker returned invalid container inspection data") from err
        labels = data.get("Config", {}).get("Labels") or {}
        compose_path = labels.get("com.docker.compose.project.config_files", "")
        listed_files = [
            str(Path(item.strip()).resolve()).casefold()
            for item in compose_path.split(",") if item.strip()
        ]
        allowed_files = {
            str(_COMPOSE_FILE).casefold(), str(_INSTRUMENTED_OVERRIDE).casefold()
        }
        if (
            data.get("Name") != f"/{container}"
            or labels.get("com.docker.compose.service") != service
            or str(_COMPOSE_FILE).casefold() not in listed_files
            or not set(listed_files) <= allowed_files
        ):
            raise ValueError("Container does not belong to this repository's Online Boutique demo")
        return data

    def stop(self, service: str) -> None:
        self._command("stop", f"boutique-{service}")

    def start(self, service: str) -> None:
        self._command("start", f"boutique-{service}")


def _state_evidence(container: dict[str, Any], state: str) -> str:
    actual = container.get("State", {}).get("Status")
    if actual != state:
        raise RuntimeError(f"Demo container was not verified {state}; observed {actual}")
    return f"Docker inspect: container={container.get('Id')} state={actual}"


def run_local_boutique_intervention_cohort(
    campaign_id: str,
    target_service: str,
    application_url: str,
    *,
    actor: str,
    controller: DockerDemoController | None = None,
) -> dict[str, Any]:
    """Stop one verified demo container, collect trials, always attempt recovery."""
    if not _COHORT_LOCK.acquire(blocking=False):
        raise ValueError("Another local demo intervention is already running")
    try:
        campaign = get_experiment_campaign(campaign_id)
        analysis = get_workflow_analysis(campaign.analysis_session_id)
        if campaign.workload_profile != ONLINE_BOUTIQUE_CHECKOUT_PROFILE:
            raise ValueError("Local Docker control requires the Online Boutique checkout profile")
        if application_url not in {"http://localhost:8080", "http://127.0.0.1:8080"}:
            raise ValueError("Local Docker control requires the local demo frontend on port 8080")
        if analysis.trace_source.endpoint.rstrip("/") not in {
            "http://localhost:16686", "http://127.0.0.1:16686"
        } or analysis.trace_source.kind.value != "jaeger":
            raise ValueError("Local Docker control requires the local demo Jaeger query endpoint")
        if target_service not in _DEMO_SERVICES or target_service not in campaign.candidate_services:
            raise ValueError("Target is not an approved candidate in this demo campaign")
        if not baseline_readiness(campaign)["ready"]:
            raise ValueError("Collect a passing baseline before the local demo intervention")
        progress = intervention_status(campaign_id)
        if any(
            item["state"] == "active" for item in progress["services"]
        ):
            raise ValueError("Restore the active intervention before starting another")
        target = next(item for item in progress["services"] if item["targetService"] == target_service)
        if target["evidenceReady"]:
            raise ValueError("This service already has a complete intervention cohort")
        remaining = target["requiredTrials"] - target["eligibleTrials"]
        if remaining <= 0 or target["trialCount"] + remaining > target["requiredTrials"] * 3:
            raise ValueError("Trial attempt limit reached; inspect excluded evidence")

        docker = controller or DockerDemoController()
        before = docker.inspect(target_service)
        _state_evidence(before, "running")
        stop_attempted = False
        outcomes = []
        try:
            stop_attempted = True
            docker.stop(target_service)
            stopped = docker.inspect(target_service)
            evidence = _state_evidence(stopped, "exited")
            record_intervention_event(campaign_id, {
                "targetService": target_service,
                "eventType": "activated",
                "operatorConfirmed": True,
                "evidence": evidence,
                "occurredAt": datetime.now(timezone.utc).isoformat(),
            }, actor=actor)
            for _ in range(remaining):
                # A missing server span is expected only while Docker itself
                # still verifies this exact demo container is stopped.
                _state_evidence(docker.inspect(target_service), "exited")
                outcome = run_campaign_intervention_trial(
                    campaign_id, target_service, application_url, actor=actor,
                    verified_unavailable_service=target_service,
                )
                outcomes.append(outcome.to_mapping())
        finally:
            if stop_attempted:
                current = docker.inspect(target_service)
                if current.get("State", {}).get("Status") != "running":
                    docker.start(target_service)
                restored = docker.inspect(target_service)
                evidence = _state_evidence(restored, "running")
                latest = intervention_status(campaign_id)
                service_state = next(
                    item for item in latest["services"]
                    if item["targetService"] == target_service
                )
                if service_state["state"] == "active":
                    record_intervention_event(campaign_id, {
                        "targetService": target_service,
                        "eventType": "restored",
                        "operatorConfirmed": True,
                        "evidence": evidence,
                        "occurredAt": datetime.now(timezone.utc).isoformat(),
                    }, actor=actor)
        return {
            "targetService": target_service,
            "outcomes": outcomes,
            "interventionStatus": intervention_status(campaign_id),
        }
    finally:
        _COHORT_LOCK.release()
