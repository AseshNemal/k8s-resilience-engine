"""Observed workflow graphs from persisted action traces, independent of labels."""

from __future__ import annotations

from typing import Any

from .execution_repository import ExecutionRepository
from .execution_models import ExecutionOutcomeStatus
from .execution_service import _repository
from .experiment_campaign_repository import ExperimentCampaignRepository
from .experiment_campaign_service import get_experiment_campaign
from .interaction_extractor import extract_interactions
from .trace_reader import parse_spans
from .trace_validator import validate_trace_quality, TraceQuality
from .sequence_evidence import sequence_evidence_blockers


def get_campaign_observed_graph(
    campaign_id: str, *,
    campaign_repository: ExperimentCampaignRepository | None = None,
    repository: ExecutionRepository | None = None,
) -> dict[str, Any]:
    """Union normal-run action graphs, retaining run/step/trace provenance.

    Never substitutes the discovery graph when run evidence is absent. Calls
    from partial traces stay visible with quality; absence proves nothing.
    """
    campaign = get_experiment_campaign(campaign_id, campaign_repository)
    runs = _repository(repository).list(batch_id=campaign.baseline.batch_id)
    services: set[str] = set()
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    complete = 0
    legacy = 0
    findings = []
    seen_traces: set[str] = set()
    for run in runs:
        run_id = run.registration.execution_id
        if run.step_evidence is None:
            legacy += 1
            findings.append({"executionId": run_id, "reason": "Historical run retains selected-request evidence only"})
            evidence = [(None, run.evidence_snapshot)] if run.evidence_snapshot else []
        else:
            blockers = sequence_evidence_blockers(run)
            complete += int(
                run.status is ExecutionOutcomeStatus.SUCCEEDED and not blockers
                and all(step.attempted and step.succeeded for step in run.step_evidence)
            )
            findings.extend({"executionId": run_id, "reason": reason} for reason in blockers)
            evidence = [(step.name, step.evidence_snapshot) for step in run.step_evidence if step.evidence_snapshot]
        for step_name, snapshot in evidence:
            if snapshot.trace_id in seen_traces:
                findings.append({"executionId": run_id, "reason": "Reused trace excluded from graph counts", "traceId": snapshot.trace_id})
                continue
            seen_traces.add(snapshot.trace_id)
            raw = snapshot.to_mapping()["rawTrace"]
            quality = validate_trace_quality([raw]).results[0]
            if quality.quality is TraceQuality.INVALID:
                findings.append({"executionId": run_id, "traceId": snapshot.trace_id, "reason": "Invalid trace excluded"})
                continue
            interaction = extract_interactions(parse_spans([raw]))[0]
            services.update(interaction.observed_services)
            for call in interaction.edges:
                key = (call.source, call.target)
                edge = edges.setdefault(key, {
                    "source": call.source, "target": call.target,
                    "classification": "unresolved", "evidence": [],
                })
                edge["evidence"].append({
                    "executionId": run_id, "stepName": step_name,
                    "traceId": snapshot.trace_id, "sha256": snapshot.sha256_digest,
                    "quality": quality.quality.value,
                    "callCount": interaction.call_counts.get(key, 0),
                    "spans": interaction.edge_evidence.get(key, []),
                })
    for edge in edges.values():
        edge["observedRunCount"] = len({item["executionId"] for item in edge["evidence"]})
    return {
        "campaignId": campaign_id, "workflowId": campaign.workflow_id,
        "scope": "captured_workflow_actions", "classification": "observed_only",
        "observedServices": sorted(services), "edges": [edges[key] for key in sorted(edges)],
        "recordedRuns": len(runs), "completeSequenceRuns": complete,
        "legacySelectedTraceRuns": legacy, "findings": findings,
        "limitations": [
            "Observed calls and requirement necessity are separate claims",
            "No service call is inferred between separate HTTP action traces",
            "Partial and historical records cannot establish sequence completeness",
        ],
    }
