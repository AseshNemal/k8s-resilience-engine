"""The recommendation for ALL workflows at once, on the connected cluster's live topology. A read-only analysis; never approvable.

Where each input comes from, all through the other components' public interfaces and none of them changed:

* frozen workflows: Component 1's approved, frozen graphs (listed as ``evidence_context`` lists them), each joined to the live topology
  by Component 3's runtime provider, which refuses a workflow whose services are not completely observed;
* declared workflows: Member 4's own declaration file (``declared_workflow``) against Component 2's discovery;
* sizing a failure: Component 3's ``predict_failure_impact`` per workflow, combined by criticality (``portfolio``);
* candidates, feasibility, simulation, ranking: ``live_pipeline.evaluate_recommendation``, the same code a single workflow uses.

A workflow that cannot be evaluated (a service it names is not completely observed) is left out and REPORTED, with the reason, under
``evidence["workflowsNotEvaluated"]``; it is never guessed. If none can be evaluated, the first reason is raised. The two sources are not
mixed: frozen and declared workflows are separate portfolios, because only a frozen workflow can ever reach the production gate.

The result carries ``PORTFOLIO_ANALYSIS_SOURCE``. No approval, apply or validation path accepts it. A person approves a candidate for ONE
named workflow, after this analysis shows which candidate protects the most.
"""
import dataclasses
import hashlib
import json
from typing import Any

from app.engine.failure_impact_prediction.runtime_provider import get_runtime_component_inputs
from app.engine.recommendation_evaluation_validation import live_pipeline
from app.engine.recommendation_evaluation_validation.approval import DECLARED_WORKFLOW_SOURCE, LIVE_EVIDENCE_SOURCE, LiveRecommendation
from app.engine.recommendation_evaluation_validation.combinations import DEFAULT_EVALUATION_BUDGET
from app.engine.recommendation_evaluation_validation.declared_workflow import (
    DeclaredWorkflowError,
    load_declared_workflows,
)
from app.engine.recommendation_evaluation_validation.evidence_context import list_frozen_workflows
from app.engine.recommendation_evaluation_validation.explanation import describe_live_recommendation
from app.engine.recommendation_evaluation_validation.live_pipeline import (
    COMPONENT,
    EvidenceProvenance,
    enrich_scheduling_facts,
    evaluate_recommendation,
    require_complete_topology,
    scheduling_evidence_for,
)
from app.engine.recommendation_evaluation_validation.portfolio import (
    NOT_APPROVABLE,
    PORTFOLIO_ANALYSIS_SOURCE,
    WorkflowPortfolio,
)
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.workflow import WorkflowGraph

PORTFOLIO_NOT_APPROVABLE = "PORTFOLIO_NOT_APPROVABLE"


def _namespace(value: str) -> str:
    namespace = (value or "").strip()
    if not namespace:
        raise ValueError("namespace must be a non-empty string")
    return namespace


def _not_evaluated(workflow_id: str, error: EvidenceError) -> dict[str, Any]:
    return {"workflowId": workflow_id, "code": error.code, "message": error.message, "details": error.details or None}


def _failing_nodes(portfolio: WorkflowPortfolio, deployment) -> dict[str, list[str]]:
    """For each workflow, the worker nodes whose loss breaks it, by Component 3's predictor (never decided here)."""
    nodes = sorted(node.name for node in deployment.nodes)
    return {workflow.workflow_id: [node for node in nodes
                                   if live_pipeline.predict_failure_impact(workflow, deployment, node).failed_workflows]
            for workflow in portfolio.workflows}


def _exposure(portfolio: WorkflowPortfolio, deployment, live: LiveRecommendation) -> dict[str, Any]:
    """Which workflows break, and at which nodes, today and after each feasible candidate.

    The ranking scores one assessed failure and the cluster average; whether a change PROTECTS a workflow is a statement about every
    worker's loss, so it is read here from the same predictor for every node. A candidate with no simulated deployment has no 'after'.
    """
    after: dict[str, Any] = {}
    for candidate in live.report.recommendations:
        simulated = ((live.details or {}).get("candidates", {}).get(candidate.id) or {}).get("simulatedDeployment")
        if candidate.feasible and simulated:
            after[candidate.id] = _failing_nodes(portfolio, DeploymentGraph.model_validate(simulated))
    return {"before": _failing_nodes(portfolio, deployment), "after": after}


def _evaluate(workflows: list[dict[str, Any]], not_evaluated: list[dict[str, Any]], first_error: EvidenceError | None, deployment,
              provenance: EvidenceProvenance, failed_node: str | None, combination_budget: int, extra: dict[str, Any]) -> LiveRecommendation:
    if not workflows:
        raise first_error or EvidenceError("RECOMMENDATION_EVIDENCE_UNAVAILABLE", "There is no workflow to recommend for.",
                                           http_status=409, component=COMPONENT)
    portfolio = WorkflowPortfolio(tuple(item["graph"] for item in workflows))
    deployment, facts = enrich_scheduling_facts(deployment, provenance.namespace)
    live = evaluate_recommendation(portfolio, deployment, provenance, failed_node,
                                   scheduling_evidence=scheduling_evidence_for(deployment), combination_budget=combination_budget)
    extra = {**extra, "workflowExposure": _exposure(portfolio, deployment, live)}
    described = [{key: value for key, value in item.items() if key != "graph"} for item in workflows]
    definitions = sorted(({**item, "graph": item["graph"].model_dump(mode="json")} for item in workflows),
                         key=lambda item: item["workflowId"])
    digest = hashlib.sha256(json.dumps(definitions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return dataclasses.replace(live, details={**live.details, "portfolioWorkflows": [item["graph"].model_dump(mode="json") for item in workflows]},
                              evidence={**live.evidence, "schedulingFacts": facts, "workflows": described,
                                               "workflowSetDigest": digest,
                                               "workflowsNotEvaluated": not_evaluated, **extra})


def build_portfolio_recommendation(namespace: str = "default", failed_node: str | None = None, *,
                                   declared: bool = False) -> LiveRecommendation:
    """An approval-capable evaluation of the complete workflow set, using each source's existing approval tier.

    The analysis-only builders remain available. This path requires complete evidence for every workflow and binds
    the whole set of definitions and freezes into the proposal fingerprint; one combination stays one proposal.
    """
    live = (build_declared_portfolio if declared else build_frozen_portfolio)(namespace, failed_node)
    if live.evidence.get("workflowsNotEvaluated"):
        raise EvidenceError("PORTFOLIO_EVIDENCE_INCOMPLETE", "Some workflows could not be evaluated. Complete their evidence "
                            "before approving a change for all workflows.", http_status=409, component=COMPONENT,
                            details={"workflows": live.evidence["workflowsNotEvaluated"]})
    evidence = {**live.evidence, "source": DECLARED_WORKFLOW_SOURCE if declared else LIVE_EVIDENCE_SOURCE,
                "workflowScope": "all"}
    if declared:
        evidence["declaredWorkflow"] = {**evidence["declaration"], "workflowId": live.report.workflow_id}
    return dataclasses.replace(live, evidence=evidence)


def build_portfolio_failures(namespace: str = "default", *, declared: bool = False) -> dict[str, Any]:
    """Assess each worker separately over one freshly loaded, complete workflow and topology snapshot."""
    live = build_portfolio_recommendation(namespace, declared=declared)
    workflow = WorkflowPortfolio(tuple(WorkflowGraph.model_validate(w) for w in live.details["portfolioWorkflows"]))
    deployment = DeploymentGraph.model_validate(live.details["currentDeployment"])
    return live_pipeline.failure_overview(workflow, deployment,
        EvidenceProvenance(live.evidence["source"], namespace, live.evidence["topologyCapturedAt"], live.evidence["topologyStatus"], None),
        scheduling_evidence=scheduling_evidence_for(deployment))


def build_frozen_portfolio(namespace: str = "default", failed_node: str | None = None, *,
                           combination_budget: int = DEFAULT_EVALUATION_BUDGET) -> LiveRecommendation:
    """Every approved, frozen workflow evaluated together on the connected cluster's live topology."""
    namespace = _namespace(namespace)
    frozen, problem = list_frozen_workflows()
    if problem:
        raise EvidenceError("WORKFLOW_STORAGE_UNAVAILABLE", problem, http_status=503, component=COMPONENT)
    if not frozen:
        raise EvidenceError("RECOMMENDATION_EVIDENCE_UNAVAILABLE",
                            "No approved, frozen workflow exists, so there is nothing to evaluate across. Freeze a workflow in Workflow "
                            "dependencies, or declare workflows for research.", http_status=409, component=COMPONENT)
    workflows, not_evaluated, first_error, deployment, captured = [], [], None, None, (None, None)
    for item in frozen:
        workflow_id = item.graph.workflow_id
        try:
            inputs = get_runtime_component_inputs(workflow_id, namespace)
        except EvidenceError as error:
            first_error = first_error or error
            not_evaluated.append(_not_evaluated(workflow_id, error))
            continue
        if deployment is None:      # one snapshot serves them all; the first complete one is the one every workflow is sized on
            deployment, captured = inputs.deployment, (inputs.topology_captured_at, inputs.topology_status)
        workflows.append({"workflowId": workflow_id, "criticality": inputs.workflow.criticality, "freezeId": inputs.workflow_freeze_id,
                          "graph": inputs.workflow})
    return _evaluate(workflows, not_evaluated, first_error, deployment,
                     EvidenceProvenance(PORTFOLIO_ANALYSIS_SOURCE, namespace, captured[0], captured[1], None),
                     failed_node, combination_budget, {"workflowBasis": "FROZEN"})


def build_declared_portfolio(namespace: str = "default", failed_node: str | None = None, *,
                             combination_budget: int = DEFAULT_EVALUATION_BUDGET,
                             declaration_path: str | None = None) -> LiveRecommendation:
    """Every DECLARED workflow evaluated together on the connected cluster's live topology (research only, like a single one)."""
    from app.engine.deployment_topology_modeling.service import discover_live

    declared = load_declared_workflows(declaration_path)
    if not declared:
        raise DeclaredWorkflowError("DECLARED_WORKFLOW_UNAVAILABLE", "No workflow is declared.", http_status=404)
    namespace = _namespace(namespace)
    discovery = discover_live(namespace=namespace)
    workflows, not_evaluated, first_error = [], [], None
    for item in declared:
        graph = item.graph.model_copy(deep=True)
        try:
            require_complete_topology(graph, discovery)
        except EvidenceError as error:
            first_error = first_error or error
            not_evaluated.append(_not_evaluated(graph.workflow_id, error))
            continue
        workflows.append({"workflowId": graph.workflow_id, "criticality": graph.criticality, "freezeId": None, "graph": graph})
    return _evaluate(workflows, not_evaluated, first_error, discovery.graph.model_copy(deep=True),
                     EvidenceProvenance(PORTFOLIO_ANALYSIS_SOURCE, namespace, discovery.captured_at, discovery.status, None),
                     failed_node, combination_budget,
                     {"workflowBasis": "DECLARED", "declaration": {"declaredBy": sorted({d.declared_by for d in declared}),
                                                                   "declarationSha256": declared[0].declaration_sha256}})


def _workflows_of(live: LiveRecommendation, candidate_id: str) -> dict[str, Any]:
    """Which workflows a candidate protects (no longer break at any worker's loss) and which it leaves exposed, and where."""
    exposure = live.evidence.get("workflowExposure") or {}
    before = {w: nodes for w, nodes in (exposure.get("before") or {}).items() if nodes}
    after = (exposure.get("after") or {}).get(candidate_id)
    if after is None:      # not feasible (nothing would change) or no prediction (unknown, so not stated)
        feasible = next((c.feasible for c in live.report.recommendations if c.id == candidate_id), False)
        return {"protected": [], "exposed": list(before) if not feasible else None, "exposedAt": before if not feasible else None}
    still = {w: nodes for w, nodes in after.items() if nodes}
    return {"protected": [w for w in before if w not in still], "exposed": list(still), "exposedAt": still}


def _workflow_risk(live: LiveRecommendation) -> list[dict[str, Any]]:
    """Each workflow as it stands today: how much it matters and the worker nodes whose loss breaks it (empty: it survives any one).

    Component 3's predictor decided this, once per worker (``_exposure``); nothing is recalculated here. Most critical first.
    """
    before = (live.evidence.get("workflowExposure") or {}).get("before") or {}
    risk = [{"workflowId": w["workflowId"], "criticality": w["criticality"], "breaksAt": list(before.get(w["workflowId"]) or [])}
            for w in live.evidence.get("workflows", [])]
    return sorted(risk, key=lambda item: (-item["criticality"], item["workflowId"]))


def _parts(live: LiveRecommendation, candidate: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The changes a candidate is made of. Approval is for one change under one workflow, so a pair is reviewed part by part."""
    if candidate["type"] == "COMBINATION":
        entry = ((live.evidence.get("combinations") or {}).get("entries") or {}).get(candidate["id"]) or {}
        members = [by_id[m] for m in entry.get("members", []) if m in by_id]
    else:
        members = [candidate]
    return [{"candidateId": m["id"], "service": m["service"], "type": m["type"], "protects": list(m["workflows"]["protected"])}
            for m in members]


def _coverage(candidate: dict[str, Any]) -> dict[str, Any] | None:
    """How the worst single-worker failure looks after the change: it protects every workflow only if that worst case is zero."""
    components = (candidate.get("ranking") or {}).get("components")
    if not components:
        return None
    after = components["worstCaseAfter"]
    return {"worstCaseBefore": components["worstCaseBefore"], "worstCaseAfter": after, "protectsEveryWorkflow": after <= 0.0}


def describe_portfolio(live: LiveRecommendation) -> dict[str, Any]:
    """The portfolio evaluation as the page shows it: the single-workflow description, with approval shut and the workflows named."""
    detail = describe_live_recommendation(live)
    analysis_only = live.evidence.get("source") == PORTFOLIO_ANALYSIS_SOURCE
    for candidate in detail["candidates"]:
        if analysis_only:
            candidate["approval"] = {"ready": False, "reason": NOT_APPROVABLE, "requiresJustification": False, "portfolio": True}
        candidate["workflows"] = _workflows_of(live, candidate["id"])
        candidate["coverage"] = _coverage(candidate)
        proposal = candidate.get("yamlProposal") or {}
        if analysis_only and proposal.get("status") == "GENERATED":
            proposal["applicable"], proposal["reason"] = False, NOT_APPROVABLE
    by_id = {c["id"]: c for c in detail["candidates"]}
    for candidate in detail["candidates"]:
        candidate["parts"] = _parts(live, candidate, by_id)
    if analysis_only and detail["candidates"]:
        detail["outcome"] = {"state": PORTFOLIO_NOT_APPROVABLE, "message": NOT_APPROVABLE}
    if analysis_only and detail["yaml"].get("available"):
        detail["yaml"].update(applicable=False, reason=NOT_APPROVABLE)
    detail["workflowExposure"] = live.evidence.get("workflowExposure")
    detail["workflowDefinitions"] = (live.details or {}).get("portfolioWorkflows")
    detail["workflowRisk"] = _workflow_risk(live)
    detail["portfolio"] = {
        "workflows": [{"workflowId": w["workflowId"], "criticality": w["criticality"]} for w in live.evidence.get("workflows", [])],
        "notEvaluated": live.evidence.get("workflowsNotEvaluated", []),
        "basis": live.evidence.get("workflowBasis"),
        "note": NOT_APPROVABLE if analysis_only else "All workflows are evaluated together. A combined fix is approved and applied as one proposal.",
    }
    return detail
