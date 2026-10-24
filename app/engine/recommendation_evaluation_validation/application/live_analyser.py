"""How each tier recalculates failure impact after a change: Member 4's adapter over the other members' public services.

Nothing here recomputes a blast radius. The workflow and the live topology are fetched the way the tier's own
recommendation was (so the comparison is like for like), and Member 3's ``predict_failure_impact`` sizes the failure:

* production tier: Member 3's runtime provider (``get_runtime_component_inputs``): the frozen workflow of Member 1 and the
  live topology of Member 2, refusing a topology too incomplete for the workflow, exactly as before the change;
* research tier: the declared workflow (Member 4's own declaration file) and Member 2's ``discover_live``, the same two
  calls ``build_declared_recommendation`` makes.

Replacement point: when Member 1 freezes a workflow for the application, the production analyser already uses it; the
research analyser is the part to retire. Both are small enough to replace with one call to a completed ``service.py``.

The comparison basis (``plan``) is fixed when the change is applied: the workflow and its identity (freeze id or
declaration digest), the namespace, the assessed failed node, the metric, and the services the change targets. The
recalculation refuses (``AnalysisUnavailable``) when the workflow it would use is no longer that one, or the assessed node
is gone, so a comparison is never presented between two different questions.
"""

from datetime import datetime, timezone
from typing import Any

from app.engine.failure_impact_prediction.runtime_provider import get_runtime_component_inputs
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.recommendation_evaluation_validation.approval import DECLARED_WORKFLOW_SOURCE, LiveRecommendation
from app.engine.recommendation_evaluation_validation.approval.research import declaration_digest
from app.engine.recommendation_evaluation_validation.application.analysis import AnalysisUnavailable
from app.engine.recommendation_evaluation_validation.declared_workflow import get_declared_workflow
from app.engine.recommendation_evaluation_validation.live_pipeline import impact_detail, require_complete_topology
from app.engine.recommendation_evaluation_validation.ranking import SINGLE_WORKFLOW

METRIC = ("Blast radius of the one workflow for the one assessed node failure, in percentage points of the workflow's "
          "weighted services (Component 3's definition, unchanged).")
OBSERVED_KIND = "recalculated-from-live-topology"


def _summary(graph: dict[str, Any] | None, services: list[str]) -> dict[str, Any]:
    """The replica counts and pod placement of the named services in a DeploymentGraph dump (camelCase, as ``_dump`` writes it)."""
    found = {s.get("name"): s for s in (graph or {}).get("services") or []}
    out: dict[str, Any] = {}
    for name in services:
        service = found.get(name)
        if service is None:
            out[name] = None
            continue
        pods = [{"pod": p.get("name"), "node": p.get("node"), "ready": p.get("ready")} for p in service.get("pods") or []]
        by_node: dict[str, int] = {}
        for pod in pods:
            by_node[pod["node"]] = by_node.get(pod["node"], 0) + 1
        out[name] = {"declaredReplicas": service.get("declaredReplicas"), "readyReplicas": service.get("readyReplicas"),
                     "pods": pods, "byNode": dict(sorted(by_node.items()))}
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Analyser:
    """What both tiers share: the plan, and the recalculation once the tier has supplied its workflow and topology."""

    tier: str

    def _load(self, basis: dict[str, Any]):
        """(workflow, deployment graph, provenance of the topology) or AnalysisUnavailable / a coded EvidenceError."""
        raise NotImplementedError

    def plan(self, change: Any, live: LiveRecommendation) -> dict[str, Any]:
        evidence = live.evidence
        candidate = next((c for c in live.report.recommendations if c.id == change.recommendation_id), None)
        detail = (live.details.get("candidates") or {}).get(change.recommendation_id) or {}
        if candidate is None or not detail:
            raise AnalysisUnavailable("CANDIDATE_NOT_IN_EVALUATION",
                                      "The evaluation that was re-checked for this apply does not describe the candidate.")
        services = list(detail.get("services") or [detail.get("service") or candidate.service])
        declared = evidence.get("declaredWorkflow") or {}
        basis = {
            "tier": self.tier, "source": evidence.get("source"), "workflowId": evidence.get("workflowId"),
            "workflowBasis": "DECLARED" if evidence.get("source") == DECLARED_WORKFLOW_SOURCE else "FROZEN",
            "workflowFreezeId": evidence.get("workflowFreezeId"), "declarationDigest": declaration_digest(evidence),
            "declaredBy": declared.get("declaredBy"),
            "namespace": evidence.get("namespace"), "failedNode": evidence.get("failedNode"),
            "failedNodeChosen": evidence.get("failedNodeChosen"), "metric": METRIC, "rankingBasis": SINGLE_WORKFLOW,
            "strategy": candidate.type, "targetServices": services, "targetDeployments": change.deployments,
            "cluster": change.cluster, "recommendationId": change.recommendation_id,
            "baselineTopologySnapshotId": evidence.get("topologySnapshotId"),
        }
        if evidence.get("workflowScope") == "all":
            basis.update(workflowScope="all", workflowSetDigest=evidence["workflowSetDigest"],
                         workflows=evidence["workflows"], rankingBasis=evidence["ranking"]["basis"],
                         metric="Criticality-weighted share of all workflows lost under the assessed worker-node failure.")
        before_graph = live.details.get("currentDeployment")
        baseline = {
            "kind": "prediction-from-live-topology",
            "blastRadius": evidence.get("blastRadiusBefore"),
            **(live.details.get("baselineImpact") or {}),
            "topologyCapturedAt": evidence.get("topologyCapturedAt"),
            "placement": _summary(before_graph, services),
        }
        predicted_impact = detail.get("predictedImpact")
        predicted = {
            "kind": "virtual-simulation",
            "blastRadius": candidate.predicted_blast_radius,
            "riskReduction": candidate.risk_reduction,
            **(predicted_impact or {}),
            "placement": _summary(detail.get("simulatedDeployment"), services),
        }
        return {"basis": basis, "baseline": baseline, "predicted": predicted}

    def recalculate(self, basis: dict[str, Any]) -> dict[str, Any]:
        if basis.get("workflowScope") == "all":
            from app.engine.recommendation_evaluation_validation.portfolio_pipeline import build_portfolio_recommendation

            live = build_portfolio_recommendation(basis["namespace"], basis["failedNode"], declared=self.tier == "research")
            if live.evidence["workflowSetDigest"] != basis.get("workflowSetDigest") or (
                self.tier == "research" and declaration_digest(live.evidence) != basis.get("declarationDigest")
            ):
                raise AnalysisUnavailable("WORKFLOW_CHANGED", "The workflow definitions changed after Apply, so the same "
                                          "all-workflows comparison cannot be made.")
            return {"kind": OBSERVED_KIND, **live.details["baselineImpact"],
                    "topologyCapturedAt": live.evidence["topologyCapturedAt"],
                    "topologyStatus": live.evidence["topologyStatus"],
                    "topologySnapshotId": live.evidence["topologySnapshotId"], "recalculatedAt": _now(),
                    "placement": _summary(live.details["currentDeployment"], list(basis.get("targetServices") or []))}
        workflow, deployment, provenance = self._load(basis)
        failed_node = basis["failedNode"]
        try:
            report = predict_failure_impact(workflow, deployment, failed_node)      # Member 3's public evaluator
        except ValueError as error:
            raise AnalysisUnavailable("ASSESSED_NODE_UNAVAILABLE", f"The assessed node {failed_node!r} is not part of the "
                                      f"freshly observed topology, so the same failure cannot be recalculated.") from error
        graph = deployment.model_dump(by_alias=True)
        return {
            "kind": OBSERVED_KIND, **impact_detail(report), **provenance,
            "topologySnapshotId": deployment.snapshot_id, "recalculatedAt": _now(),
            "placement": _summary(graph, list(basis.get("targetServices") or [])),
        }


class ProductionAnalyser(_Analyser):
    tier = "production"

    def _load(self, basis: dict[str, Any]):
        inputs = get_runtime_component_inputs(basis["workflowId"], basis["namespace"])
        if basis.get("workflowFreezeId") and inputs.workflow_freeze_id != basis["workflowFreezeId"]:
            raise AnalysisUnavailable(
                "WORKFLOW_CHANGED", "The workflow was frozen again after this change was applied, so the failure impact now would "
                "be measured against a different workflow definition than the one used before. No comparison is made.")
        return inputs.workflow, inputs.deployment, {
            "topologyCapturedAt": inputs.topology_captured_at, "topologyStatus": inputs.topology_status,
            "workflowFreezeId": inputs.workflow_freeze_id}


class ResearchAnalyser(_Analyser):
    tier = "research"

    def _load(self, basis: dict[str, Any]):
        from app.engine.deployment_topology_modeling.service import discover_live

        declared = get_declared_workflow(basis["workflowId"])
        if basis.get("declarationDigest") and declared.declaration_sha256 != basis["declarationDigest"]:
            raise AnalysisUnavailable(
                "WORKFLOW_CHANGED", "The declared workflow was edited after this change was applied, so the failure impact now "
                "would be measured against a different declaration than the one used before. No comparison is made.")
        discovery = discover_live(namespace=basis["namespace"])
        require_complete_topology(declared.graph, discovery)
        return declared.graph.model_copy(deep=True), discovery.graph.model_copy(deep=True), {
            "topologyCapturedAt": discovery.captured_at, "topologyStatus": discovery.status,
            "declarationDigest": declared.declaration_sha256}


__all__ = ["ProductionAnalyser", "ResearchAnalyser"]
