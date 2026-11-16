"""Read-only operational evidence selection for Recommendations and Overview.

Only exact workflow definitions explicitly reviewed for the current cluster UID,
connection and namespace can drive workflow predictions. Historical declarations
remain separately identified. Without a current review, Member 3 service exposure
and Member 4 placement suggestions are available; workflow outcomes remain unknown.
"""
from types import SimpleNamespace
from typing import Any

from app.engine.deployment_topology_modeling.discovery_all import discover_all
from app.engine.recommendation_evaluation_validation.declared_workflow import (
    NOT_OBSERVED,
    load_research_workflows,
)
from app.engine.workflow_dependency_modeling import service as workflow_service
from app.shared.evidence_errors import EvidenceError

AVAILABLE = "available"
MISSING = "missing"
INCOMPLETE = "incomplete"
PENDING = "pending"

# Where a person obtains a frozen workflow. Component 1 owns this; the text names its steps and invents none.
FREEZE_NEXT_ACTION = (
    "Open Workflow Discovery. Inspect observed calls or a real application definition, review dependency roles, and "
    "explicitly enable operational analysis for this exact cluster and application."
)


def _item(identifier: str, label: str, state: str, detail: str, next_action: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"id": identifier, "label": label, "state": state, "detail": detail, "nextAction": next_action, **extra}


def _referenced_services(graph) -> list[str]:
    names = set(graph.mandatory_services) | set(graph.optional_services)
    for edge in graph.edges:
        names.update((edge.source, edge.target))
    return sorted(names)


def _namespace_summaries(overview) -> list[dict[str, Any]]:
    rows = []
    for result in overview.namespaces:
        services = sorted(service.name for service in result.graph.services)
        if not services:
            continue
        rows.append({
            "namespace": result.namespace,
            "services": services,
            "status": result.status,
            "incompleteServices": sorted(
                name for name, evidence in result.service_evidence.items() if not evidence.complete),
        })
    return rows


def _choose_namespace(requested: str | None, rows: list[dict[str, Any]]) -> tuple[str | None, str]:
    """The namespace to use and why: asked for, the only application, none, or ambiguous. Never a guess."""
    names = [row["namespace"] for row in rows]
    if requested:
        return (requested, "requested") if requested in names else (None, "unknown")
    if len(names) == 1:
        return names[0], "only-application"
    return None, "none" if not names else "ambiguous"


def _frozen_workflows(namespace: str | None = None, fingerprint: str | None = None) -> tuple[list[Any], str | None]:
    """Only definitions explicitly reviewed for the current cluster and application."""
    if not namespace:
        return [], None
    try:
        return workflow_service.list_operational_workflows(namespace, fingerprint), None
    except EvidenceError as error:
        return [], error.message
    except Exception:
        return [], "The approved workflow store could not be read."


def _describe_workflow(frozen, namespace_row: dict[str, Any] | None) -> dict[str, Any]:
    graph = frozen.graph
    referenced = _referenced_services(graph)
    present = set(namespace_row["services"]) if namespace_row else set()
    missing = [name for name in referenced if name not in present]
    incomplete = [name for name in referenced if namespace_row and name in namespace_row["incompleteServices"]]
    frozen_at = getattr(frozen, "frozen_at", None)
    reason = None
    if namespace_row is None:
        reason = "No application is selected."
    elif missing:
        reason = f"Services not found in {namespace_row['namespace']}: {', '.join(missing)}."
    elif incomplete:
        reason = f"The topology of these services is not fully resolved: {', '.join(incomplete)}."
    return {
        "workflowId": graph.workflow_id,
        "freezeId": getattr(frozen, "freeze_id", None),
        "assessmentId": getattr(frozen, "assessment_id", None),
        "frozenAt": frozen_at.isoformat().replace("+00:00", "Z") if hasattr(frozen_at, "isoformat") else None,
        "criticality": graph.criticality,
        "mandatoryServices": list(graph.mandatory_services),
        "services": referenced,
        "applicable": bool(referenced) and reason is None,
        "missingServices": missing,
        "incompleteServices": incomplete,
        "reason": reason if referenced else "The frozen graph names no services.",
        "workflowEvidence": getattr(frozen, "operational_review", None),
    }


def _describe_declared(declared, namespace_row: dict[str, Any] | None) -> dict[str, Any]:
    """A declared workflow, described like a frozen one, and always labelled: it is not observed and cannot be approved."""
    described = _describe_workflow(SimpleNamespace(graph=declared.graph), namespace_row)
    if declared.namespace and (namespace_row or {}).get("namespace") != declared.namespace:
        described.update(applicable=False, reason="This declaration belongs to namespace " + declared.namespace)
    return {**described, "freezeId": None, "frozenAt": None, "declared": True, "approvable": False,
            "sourceName": declared.source_name, "namespace": declared.namespace, "note": declared.note,
            "declaredBy": declared.declared_by, "declarationSha256": declared.declaration_sha256, "limits": NOT_OBSERVED}


def _declared_workflows(row: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    """Declared workflows from Member 4's own file (never a frozen one), or the reason the file could not be used."""
    try:
        return [_describe_declared(item, row) for item in load_research_workflows()], None
    except EvidenceError as error:
        return [], {"code": error.code, "message": error.message}


def build_recommendation_context(namespace: str | None = None, workflow_id: str | None = None) -> dict[str, Any]:
    """The evidence checklist for the Recommendations page. Read-only; it answers even when evidence is missing."""
    namespace = (namespace or "").strip() or None
    workflow_id = (workflow_id or "").strip() or None
    evidence: list[dict[str, Any]] = []
    actions: list[str] = []

    # 1. the connected cluster and the applications it holds (Component 2)
    cluster: dict[str, Any] = {"state": MISSING, "fingerprint": None}
    rows: list[dict[str, Any]] = []
    try:
        overview = discover_all(include_system=False)
        rows = _namespace_summaries(overview)
        cluster = {"state": AVAILABLE, "fingerprint": overview.cluster.connection_fingerprint, "capturedAt": overview.captured_at}
        evidence.append(_item("cluster", "Connected cluster", AVAILABLE,
                              f"The cluster answered; {len(rows)} namespace(s) hold applications."))
    except EvidenceError as error:
        cluster = {"state": MISSING, "fingerprint": None, "code": error.code}
        evidence.append(_item("cluster", "Connected cluster", MISSING, error.message,
                              "Connect a cluster on the Connect page.", code=error.code))
        actions.append("Connect a cluster on the Connect page.")

    # 2. the application (namespace)
    chosen, choice = _choose_namespace(namespace, rows)
    row = next((r for r in rows if r["namespace"] == chosen), None)
    if cluster["state"] == AVAILABLE:
        if row:
            how = {"requested": "chosen by you", "only-application": "the only namespace with applications"}[choice]
            evidence.append(_item("application", "Application", AVAILABLE,
                                  f"{chosen} ({how}): {', '.join(row['services'])}."))
        elif choice == "unknown":
            evidence.append(_item("application", "Application", MISSING,
                                  f"The namespace {namespace} was not found among the cluster's applications.",
                                  "Choose one of the discovered namespaces."))
            actions.append("Choose one of the discovered namespaces.")
        elif choice == "ambiguous":
            evidence.append(_item("application", "Application", MISSING,
                                  f"{len(rows)} namespaces hold applications, so none was chosen for you.",
                                  "Choose the application to recommend for."))
            actions.append("Choose the application to recommend for.")
        else:
            evidence.append(_item("application", "Application", MISSING,
                                  "The cluster has no namespace with applications.",
                                  "Deploy an application, then reload."))
            actions.append("Deploy an application, then reload.")

        # 3. how complete the topology is for it (Component 2)
        if row:
            if row["incompleteServices"] or row["status"] == "partial":
                evidence.append(_item("topology", "Deployment topology", INCOMPLETE,
                                      "Placement is not fully resolved for: "
                                      f"{', '.join(row['incompleteServices']) or 'part of the namespace'}.",
                                      "Open Deployment topology to see the unresolved items; a recommendation needs "
                                      "the services a workflow uses to be fully placed."))
            else:
                evidence.append(_item("topology", "Deployment topology", AVAILABLE,
                                      f"Every service in {chosen} has an observed placement."))

    # 4. approved frozen workflows (Component 1)
    frozen, store_problem = _frozen_workflows(chosen, cluster.get("fingerprint")) if chosen else ([], None)
    workflows = [_describe_workflow(item, row) for item in frozen]
    applicable = [w for w in workflows if w["applicable"]]
    if store_problem:
        evidence.append(_item("workflow", "Operational workflow definition", MISSING, store_problem,
                              "Check that the extension backend and its database are running."))
        actions.append("Check that the extension backend and its database are running.")
    elif not workflows:
        evidence.append(_item("workflow", "Operational workflow definition", MISSING,
                              "No workflow definition has a current operational review for this cluster and application. "
                              "Workflow survivability is unknown. Deployment-level analysis is available.",
                              "Open Workflow Discovery, review a real definition and explicitly enable operational analysis."))
        actions.append("Open Workflow Discovery, review a real definition and explicitly enable operational analysis.")
    elif row and not applicable:
        evidence.append(_item("workflow", "Operational workflow definition", MISSING,
                              f"{len(workflows)} frozen workflow(s) exist, but none covers the services in {chosen}.",
                              "Freeze a workflow whose services are in this application: " + FREEZE_NEXT_ACTION))
        actions.append("Freeze a workflow whose services are in this application: " + FREEZE_NEXT_ACTION)
    elif applicable:
        evidence.append(_item("workflow", "Operational workflow definition", AVAILABLE,
                              f"{len(applicable)} of {len(workflows)} reviewed real workflow definition(s) apply to {chosen}."))
    else:
        evidence.append(_item("workflow", "Operational workflow definition", AVAILABLE,
                              f"{len(workflows)} frozen workflow(s) exist; choose an application to see which apply."))

    # 5. the workflow to use: the one asked for, or the only one that applies. Never a guess between several.
    selected = None
    if workflow_id:
        selected = workflow_id if any(w["workflowId"] == workflow_id and w["applicable"] for w in workflows) else None
        selection = "requested" if selected else "unavailable"
    elif len(applicable) == 1:
        selected, selection = applicable[0]["workflowId"], "only-applicable"
    else:
        selection = "none" if not applicable else "choose"

    # 6. With no applicable workflow, show Component 2's deployment-only evidence without inventing one.
    deployment_findings = None
    if row and not applicable:
        observed = next(result for result in overview.namespaces if result.namespace == chosen)
        if observed.graph.nodes and row["status"] != "partial" and not row["incompleteServices"]:
            from app.engine.recommendation_evaluation_validation.deployment_recommendations import build_deployment_recommendations
            deployment_findings = build_deployment_recommendations(observed)

    impact_detail = (
        "Computed by the failure-impact predictor from the frozen workflow and the live topology when the recommendation is requested."
        if selected else
        "Per-node service exposure and placement suggestions are shown below. Workflow survival requires a current review of a real workflow definition."
        if deployment_findings else
        "Complete deployment placement and workflow evidence are required to compute failure impact."
    )

    evidence.append(_item(
        "failureImpact",
        "Deployment-level failure impact" if deployment_findings else "Failure impact",
        AVAILABLE if deployment_findings else PENDING if selected else MISSING,
        impact_detail,
        None if selected or deployment_findings else "Select an application and a frozen workflow that applies to it."))

    # 7. declared workflows: Member 4's own source when Component 1 has none. Listed apart, labelled, never selected for you, never approvable.
    unverified, declared_problem = _declared_workflows(row)
    declared = []  # Legacy declarations remain research/history, never operational choices.
    if unverified:
        evidence.append(_item("unverifiedWorkflow", "Preserved declarations", INCOMPLETE,
            f"{len(unverified)} file/editor declaration(s) are preserved as research or history and excluded from operational analysis without a current review."))
    if declared_problem:
        evidence.append(_item("declaredWorkflow", "Research declaration", MISSING, declared_problem["message"],
                              "Fix the declaration file named by RESILIENCE_DECLARED_WORKFLOW_PATH.", code=declared_problem["code"]))
    elif declared:
        usable = [d for d in declared if d["applicable"]]
        evidence.append(_item("declaredWorkflow", "Declared workflow", AVAILABLE if usable else MISSING,
                              f"{len(usable)} of {len(declared)} declared workflow(s) apply to {chosen or 'the selected application'}. "
                              "Declared, not observed: recommendations from it can be read; approving one is a research-only step.",
                              None if usable else "Declare a workflow whose services are in this application."))

    ready = bool(cluster["state"] == AVAILABLE and row and selected)
    return {
        "cluster": cluster,
        "namespaces": rows,
        "namespace": chosen,
        "namespaceChoice": choice,
        "workflows": workflows,
        "declaredWorkflows": declared,
        "unverifiedWorkflows": [{**w, "applicable": False, "operationalEligible": False,
                                 "reason": (w.get("reason") or "") + " Historical or unverified declaration; current operational review required."}
                                for w in unverified],
        "workflowEvidencePolicy": "operational-workflow/v1",
        "declaredWorkflowsError": declared_problem,
        "deploymentFindings": deployment_findings,
        "workflowId": selected,
        "workflowChoice": selection,
        "evidence": evidence,
        "ready": ready,
        "nextActions": actions,
        "readOnly": True,
    }


list_frozen_workflows = _frozen_workflows      # public name for the portfolio evaluation, which needs every frozen workflow too
