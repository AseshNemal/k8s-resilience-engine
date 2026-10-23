"""The Development Lab's read-only view over Member 4's real pipeline.

Every number here is produced by the existing engine, never recomputed in this module:

* candidates, feasibility, virtual simulation, ranking and evidence come from
  ``live_pipeline.evaluate_recommendation`` (through the scenario's ``evaluate``);
* the page-ready candidate detail comes from ``explanation.describe_live_recommendation``;
* the current risk comes from Component 3's public ``predict_failure_impact``;
* YAML proposals come from ``yaml_proposal.build_yaml_proposal`` (always ``applicable=False`` here,
  because the provenance of every lab evaluation is ``offline-test-fixture``);
* offline validation walks the real approval gate and ``ValidationRun`` exactly like
  ``experiments/member4_offline_benchmark``, against the scripted in-memory test cluster.

Nothing in this module reads a cluster, writes to a cluster, or loads live evidence.
"""

from typing import Any

from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.recommendation_evaluation_validation.explanation import describe_live_recommendation
from app.engine.recommendation_evaluation_validation.ranking import RankingWeights
from app.engine.recommendation_evaluation_validation.validation import (
    APPLY,
    FAIL_NODE,
    TestTarget,
    ValidationPlan,
    ValidationRun,
    authorize,
)
from app.engine.recommendation_evaluation_validation.yaml_proposal import GENERATED, build_yaml_proposal
from tests.support.member4_approvals import requested_for_test
from tests.support.member4_harness import OFFLINE_SOURCE
from tests.support.member4_scenarios import Case, catalogue
from tests.support.member4_test_cluster import FakeTestCluster, spread, stay

TARGET = TestTarget("offline-double", "test")

LAB_LABEL = (
    "OFFLINE DEVELOPMENT LAB: controlled hand-written scenarios and a scripted scheduler. "
    "Nothing here is read from, predicted about, or applied to a real cluster."
)

VALIDATION_LABEL = (
    "Controlled offline validation: the 'test cluster' is an in-memory double whose scheduler is scripted. "
    "Under 'schedulerSpreads' it places pods the way a working constraint tends to, so a prediction error of 0 "
    "only proves the comparison arithmetic, not the model. 'schedulerChangesNothing' shows what the comparison "
    "reports when the change has no effect."
)

BASELINES = [
    {"name": "Replica-count-only", "evaluated": False,
     "reason": "Not implemented. Counting replicas cannot see placement, so it cannot express the problem these "
               "scenarios isolate (several ready replicas on one failing node)."},
    {"name": "Deployment-only (exposure without workflow dependencies)", "evaluated": False,
     "reason": "Not implemented for this benchmark. Member 2 quantifies exposure and concentration, but no "
               "baseline variant ranks recommendations on topology alone."},
    {"name": "Dependency-only (workflow graph without placement)", "evaluated": False,
     "reason": "Not implemented. Without placement, every node failure looks identical, so no per-candidate "
               "before/after difference can be computed."},
    {"name": "Proposed cross-layer method (workflow dependencies x pod placement)", "evaluated": True,
     "reason": "This is what every scenario row evaluates: candidates are generated from cross-layer root causes "
               "and sized by Component 3's predictor before and after the virtual change."},
]


class ScenarioNotFound(KeyError):
    """No controlled scenario with this name exists in the catalogue."""


def _find_case(name: str) -> Case:
    for case in catalogue():
        if case.name == name:
            return case
    raise ScenarioNotFound(name)


def list_scenarios() -> list[dict[str, Any]]:
    """The catalogue as selection metadata: no evaluation is run."""
    return [
        {
            "name": case.name,
            "objective": case.scenario.description or case.covers,
            "situation": case.covers,
            "judgedService": case.service,
            "judgedStrategy": case.strategy,
            "expected": {
                "feasibility": case.feasibility,
                "yaml": case.yaml,
                "selected": case.selected,
                "documents": case.documents,
            },
        }
        for case in catalogue()
    ]


def _scenario_inputs(case: Case) -> dict[str, Any]:
    """Section A: the supplied artifacts, exactly as the scenario defines them."""
    scenario = case.scenario
    workflow = scenario.workflow
    deployment = scenario.deployment
    services = []
    for service in deployment.services:
        intent = service.declared_intent
        services.append({
            "name": service.name,
            "namespace": service.namespace,
            "declaredReplicas": service.declared_replicas,
            "readyReplicas": service.ready_replicas,
            "replicaConcentration": service.replica_concentration,
            "concentrated": service.concentrated,
            "hostingNodes": list(service.hosting_nodes),
            "pods": [{"name": pod.name, "node": pod.node, "ready": pod.ready} for pod in service.pods],
            "intent": None if intent is None else {
                "resourceRequests": intent.resource_requests,
                "nodeSelector": intent.node_selector,
                "tolerations": intent.tolerations,
                "topologySpreadConstraints": intent.topology_spread_constraints,
                "podAntiAffinityConfigured": intent.pod_anti_affinity_configured,
            },
        })
    taint_evidence = {
        node.name: ("unread" if node.name not in scenario.evidence.node_taints
                    else list(scenario.evidence.node_taints[node.name]))
        for node in deployment.nodes
    }
    return {
        "name": case.name,
        "objective": scenario.description or case.covers,
        "situation": case.covers,
        "workflow": {
            "workflowId": workflow.workflow_id,
            "criticality": workflow.criticality,
            "mandatoryServices": list(workflow.mandatory_services),
            "optionalServices": list(workflow.optional_services),
            "edges": [{"source": edge.source, "target": edge.target} for edge in workflow.edges],
        },
        "extraWorkflows": [
            {"workflowId": extra.workflow_id, "mandatoryServices": list(extra.mandatory_services)}
            for extra in scenario.extra_workflows
        ],
        "deployment": {
            "snapshotId": deployment.snapshot_id,
            "failureDomainType": deployment.cluster.failure_domain_type,
            "nodes": [
                {
                    "name": node.name, "zone": node.zone, "ready": node.ready,
                    "schedulable": node.schedulable, "cpuAvailable": node.cpu_available,
                    "memoryAvailable": node.memory_available, "labels": node.labels,
                }
                for node in deployment.nodes
            ],
            "services": services,
        },
        "failedNode": scenario.failed_node,
        "schedulingEvidence": {
            "nodeTaints": taint_evidence,
            "note": "A node listed as 'unread' means taints were never observed for it; the feasibility check "
                    "must then report INSUFFICIENT_EVIDENCE rather than guess.",
        },
    }


def _validation_for(case: Case) -> dict[str, Any]:
    """Section G: the real approval gate and ValidationRun against the scripted double, as in the benchmark."""
    probe = case.scenario.evaluate()
    selected = probe.report.selected_recommendation_id
    if not selected:
        return {"available": False,
                "reason": "No candidate was selected under the default ranking weights, so there is no approved "
                          "change to validate."}
    proposal = build_yaml_proposal(probe, selected)
    if proposal.status != GENERATED:
        return {"available": False,
                "reason": f"The selected candidate has no applicable proposal ({proposal.status}: "
                          f"{proposal.reason}), so there is nothing to validate."}

    service, live, recommendation_id = requested_for_test(case.scenario)
    approval = service.approve(recommendation_id, "v1", "", decided_by="dev-lab", evaluate=lambda _: live)
    plan = ValidationPlan.from_approval(approval, live, case.scenario.workflow, TARGET)
    runs = {}
    for key, scheduler in (("schedulerSpreads", spread), ("schedulerChangesNothing", stay)):
        cluster = FakeTestCluster(case.scenario.deployment, name=TARGET.name, scheduler=scheduler)
        result = ValidationRun(plan, cluster).execute(
            authorize(plan, APPLY, "dev-lab"), authorize(plan, FAIL_NODE, "dev-lab"))
        runs[key] = result.to_dict()
    return {
        "available": True,
        "label": VALIDATION_LABEL,
        "recommendationId": recommendation_id,
        "target": {"name": TARGET.name, "environment": TARGET.environment},
        "approvalGate": (
            "The approval below was produced by the real ApprovalService from an offline evaluation relabelled "
            "by the test-only helper tests/support/member4_approvals.as_live. That relabelling is what lets the "
            "scripted validation walk the real gate; no lab route can do it, and no lab output is approvable."
        ),
        "baseline": "DERIVED_FROM_PLACEMENT (no pre-change failure was injected; the benchmark compares the "
                    "post-change observation against the prediction).",
        "runs": runs,
    }


def scenario_detail(name: str, weights: RankingWeights | None = None) -> dict[str, Any]:
    """One scenario evaluated end to end: inputs, current risk, candidates, simulation, ranking, YAML, validation."""
    case = _find_case(name)
    options = {"ranking_weights": weights} if weights is not None else {}
    live = case.scenario.evaluate(**options)
    failed_node = live.evidence["failedNode"]
    before = predict_failure_impact(case.scenario.workflow, case.scenario.deployment, failed_node)

    return {
        "label": LAB_LABEL,
        "provenance": {
            "source": live.evidence["source"],
            "trustedLiveSource": "live-discovery",
            "isLiveEvidence": live.evidence["source"] == "live-discovery",
            "note": "Every lab evaluation is stamped 'offline-test-fixture'. The approval gate and the YAML "
                    "generator trust only 'live-discovery', which is why nothing here is approvable or applicable.",
        },
        "inputs": _scenario_inputs(case),
        "currentRisk": before.model_dump(by_alias=True),
        "detail": describe_live_recommendation(live),
        "extras": {
            candidate_id: {"addedReplicas": list(candidate.get("addedReplicas", []))}
            for candidate_id, candidate in (live.details or {}).get("candidates", {}).items()
        },
        "feasibility": live.evidence["feasibility"],
        "ranking": live.evidence["ranking"],
        "combinations": live.evidence["combinations"],
        "validation": _validation_for(case),
        "customWeights": weights.to_dict() if weights is not None else None,
    }


def _detected_problem(case: Case) -> dict[str, Any] | None:
    scenario = case.scenario
    live = scenario.evaluate()
    failed_node = live.evidence["failedNode"]
    before = predict_failure_impact(scenario.workflow, scenario.deployment, failed_node)
    if not before.failed_services and not before.root_causes:
        return None
    return {
        "failedNode": failed_node,
        "blastRadius": before.blast_radius,
        "survivability": before.survivability,
        "failedServices": list(before.failed_services),
        "failedWorkflows": list(before.failed_workflows),
        "rootCauses": [cause.model_dump(by_alias=True) for cause in before.root_causes],
    }


def _judged_service_inputs(case: Case) -> dict[str, Any]:
    service = next((s for s in case.scenario.deployment.services if s.name == case.service), None)
    if service is None:
        return {"service": case.service}
    return {
        "service": service.name,
        "replicas": f"{service.ready_replicas}/{service.declared_replicas} ready",
        "placement": sorted({pod.node for pod in service.pods}),
        "concentration": service.replica_concentration,
    }


def experiment_overview() -> dict[str, Any]:
    """Task 3's comparison: every scenario, what the engine did with it, and the scripted-validation outcome."""
    rows = []
    for case in catalogue():
        live = case.scenario.evaluate()
        candidates = [
            {
                "id": item.id, "type": item.type, "service": item.service, "feasible": item.feasible,
                "score": item.score, "riskReduction": item.risk_reduction,
                "predictedBlastRadius": item.predicted_blast_radius,
            }
            for item in live.report.recommendations
        ]
        validation = _validation_for(case)
        runs = validation["runs"] if validation["available"] else None
        rows.append({
            "scenario": case.name,
            "situation": case.covers,
            "inputs": _judged_service_inputs(case),
            "detectedProblem": _detected_problem(case),
            "candidates": candidates,
            "selected": live.report.selected_recommendation_id,
            "validation": runs and {
                "recommendationId": validation["recommendationId"],
                "schedulerSpreads": {
                    "validated": runs["schedulerSpreads"]["validated"],
                    "predictionError": runs["schedulerSpreads"]["predictionError"],
                },
                "schedulerChangesNothing": {
                    "validated": runs["schedulerChangesNothing"]["validated"],
                    "predictionError": runs["schedulerChangesNothing"]["predictionError"],
                },
            },
            "expected": {
                "feasibility": case.feasibility, "yaml": case.yaml, "selected": case.selected,
                "strategy": case.strategy, "documents": case.documents,
            },
        })
    generated = [row for row in rows if row["validation"]]
    return {
        "label": LAB_LABEL,
        "rows": rows,
        "baselines": BASELINES,
        "metrics": {
            "scenarios": len(rows),
            "withSelectedRecommendation": sum(1 for row in rows if row["selected"]),
            "withGeneratedYaml": len(generated),
            "validatedWhenSchedulerSpreads": sum(
                1 for row in generated if row["validation"]["schedulerSpreads"]["validated"]),
            "predictionErrorsWhenSchedulerSpreads": [
                row["validation"]["schedulerSpreads"]["predictionError"] for row in generated],
        },
        "demonstrates": (
            "That the engine behaves as designed on controlled inputs: candidates are generated from root causes, "
            "infeasible and unverifiable changes are refused, the selected change's predicted effect is computed by "
            "Component 3's predictor, and the comparison arithmetic reports agreement or error against a scripted "
            "observation. It does not measure accuracy against a real cluster; that needs a real test cluster and "
            "a controlled node failure."
        ),
    }
