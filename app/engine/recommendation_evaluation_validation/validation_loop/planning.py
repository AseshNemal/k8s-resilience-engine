"""Turning an evaluation on the target cluster into a bounded plan the person can approve: candidates, workers, issues, a digest.

Everything here is derived from live evidence on THE TARGET (the recommendation is recomputed on the target's own topology; a
result from another cluster is never reused). The plan lists, in plain terms, what would happen: which changes would be tried, which
workers stopped how many times, what else on the cluster would be disrupted, and what stops the run from being approved. The digest
binds an approval to exactly that plan: anything that changes between planning and approving (a node, a candidate's YAML, the
disruption findings) changes the digest, and the approval is refused as stale.

The plan never carries a prediction that was not made by Component 3 through ``predict_outcome``.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app.engine.recommendation_evaluation_validation.approval import LiveRecommendation
from app.engine.recommendation_evaluation_validation.approval.eligibility import evaluate_eligibility
from app.engine.recommendation_evaluation_validation.validation.models import ValidationRefusedError
from app.engine.recommendation_evaluation_validation.validation.prediction import predict_outcome
from app.engine.recommendation_evaluation_validation.validation_loop.disruption import DisruptionReport, PodFact, analyse
from app.engine.recommendation_evaluation_validation.validation_loop.environment import ProbeDefinition, TargetIdentity
from app.engine.recommendation_evaluation_validation.validation_loop.fault_readiness import FaultReadiness
from app.engine.recommendation_evaluation_validation.validation_loop.loop import LoopCandidate
from app.engine.recommendation_evaluation_validation.validation_loop.models import Mode, ModePlan
from app.engine.recommendation_evaluation_validation.yaml_proposal import GENERATED, build_yaml_proposal
from app.shared.schemas.workflow import WorkflowGraph

COST_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


@dataclass
class Issue:
    """Something that stops the run from being approved (``blocking``) or that the person should know (``note``)."""

    code: str
    message: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "blocking": self.blocking}


@dataclass
class CandidatePlan:
    loop_candidate: LoopCandidate
    strategy: str
    service: str
    cost: str
    rank: int | None
    predicted_blast_radius: float
    risk_reduction: float

    def to_dict(self) -> dict[str, Any]:
        c = self.loop_candidate
        return {"id": c.candidate_id, "strategy": self.strategy, "service": self.service, "cost": self.cost, "rank": self.rank,
                "yamlSha256": c.yaml_sha256, "predictedBlastRadius": self.predicted_blast_radius, "riskReduction": self.risk_reduction,
                "predictedPerWorker": {w: {"blastRadiusAfter": p.after.blast_radius, "blastRadiusBefore": p.before.blast_radius,
                                           "workflowSurvives": p.workflow_id not in p.after.failed_workflows}
                                       for w, p in c.predicted.items()}}


@dataclass
class LoopPlan:
    mode_plan: ModePlan
    identity: TargetIdentity
    namespace: str
    workflow_id: str
    workflow_basis: str
    probe: ProbeDefinition | None
    candidates: list[CandidatePlan]
    disruption: dict[str, DisruptionReport]
    readiness: FaultReadiness | None
    issues: list[Issue]
    estimate: dict[str, Any] | None
    digest: str
    riskiest_worker: str | None
    skipped_candidates: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> list[Issue]:
        return [i for i in self.issues if i.blocking]

    @property
    def can_approve(self) -> bool:
        return not self.blocking

    @property
    def acknowledgements_needed(self) -> list[str]:
        needed = {f.workload for r in self.disruption.values() for f in r.to_acknowledge}
        return sorted(needed)

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode_plan.to_dict(), "target": {"kind": self.identity.kind, "name": self.identity.name,
                                                             "clusterUid": self.identity.cluster_uid, "apiServer": self.identity.api_server,
                                                             "nodes": list(self.identity.node_names)},
                "namespace": self.namespace, "workflowId": self.workflow_id, "workflowBasis": self.workflow_basis,
                "probe": self.probe.to_dict() if self.probe else None,
                "candidates": [c.to_dict() for c in self.candidates], "skippedCandidates": self.skipped_candidates,
                "disruption": {w: r.to_dict() for w, r in self.disruption.items()},
                "acknowledgementsNeeded": self.acknowledgements_needed,
                "faultReadiness": self.readiness.to_dict() if self.readiness else None,
                "issues": [i.to_dict() for i in self.issues], "canApprove": self.can_approve, "estimate": self.estimate,
                "riskiestWorker": self.riskiest_worker, "planDigest": self.digest, "notes": self.notes}


def select_candidates(live: LiveRecommendation, workflow: WorkflowGraph, workers: list[str], limit: int
                      ) -> tuple[list[CandidatePlan], list[dict[str, str]]]:
    """The best ``limit`` candidates that can actually be proven: ranked, feasible, with generated YAML and a prediction for every worker.

    Anything dropped is returned with the reason, so the plan never silently shrinks.
    """
    skipped: list[dict[str, str]] = []
    usable: list[CandidatePlan] = []
    for candidate in live.report.recommendations:
        standing = evaluate_eligibility(live, candidate.id)
        if not standing.eligible:
            skipped.append({"id": candidate.id, "reason": standing.reason if hasattr(standing, "reason") else "not eligible"})
            continue
        proposal = build_yaml_proposal(live, candidate.id)
        if proposal.status != GENERATED:
            skipped.append({"id": candidate.id, "reason": f"no YAML could be generated ({proposal.reason})"})
            continue
        try:
            predicted = {w: predict_outcome(workflow, live, candidate.id, failed_node=w) for w in workers}
        except ValidationRefusedError as error:
            skipped.append({"id": candidate.id, "reason": f"{error.code}: {error.message}"})
            continue
        cost = COST_ORDER.get(candidate.resource_cost, 3)
        loop_candidate = LoopCandidate(candidate.id, proposal.yaml, proposal.sha256, predicted, (cost, standing.rank))
        usable.append(CandidatePlan(loop_candidate, candidate.type, candidate.service, candidate.resource_cost, standing.rank,
                                    candidate.predicted_blast_radius, candidate.risk_reduction))
    usable.sort(key=lambda c: (c.rank if c.rank is not None else 10**6, c.loop_candidate.change_key))
    return usable[:limit], skipped


def plan_digest(parts: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def digest_parts(*, identity: TargetIdentity, namespace: str, workflow_id: str, mode_plan: ModePlan, candidates: list[CandidatePlan],
                 probe: ProbeDefinition | None, disruption: dict[str, DisruptionReport], critical_namespaces: tuple[str, ...]) -> dict[str, Any]:
    return {
        "target": {"kind": identity.kind, "name": identity.name, "uid": identity.cluster_uid, "api": identity.api_server,
                   "nodes": sorted(identity.node_names)},
        "namespace": namespace, "workflow": workflow_id, "mode": mode_plan.to_dict(),
        "candidates": [{"id": c.loop_candidate.candidate_id, "yaml": c.loop_candidate.yaml_sha256} for c in candidates],
        "probe": probe.to_dict() if probe else None, "critical": list(critical_namespaces),
        "disruption": {w: sorted((f.workload, f.severity) for f in r.findings) for w, r in sorted(disruption.items())},
    }


def disruption_for(workers: list[str], pods: list[dict[str, Any]], namespace: str, critical: tuple[str, ...]) -> dict[str, DisruptionReport]:
    facts = [PodFact.from_dict(item) for item in pods]
    return {w: analyse(w, facts, namespace, critical) for w in workers}
