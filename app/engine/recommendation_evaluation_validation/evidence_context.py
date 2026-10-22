"""What the Recommendations page needs to know before it can ask for a recommendation.

The page used to ask the person for a cluster namespace and a frozen workflow ID. Both are things the system already
knows: the connected cluster reports which namespaces hold applications, and Component 1 keeps the approved, frozen
workflow graphs. This module reads them, read-only, through the other components' public service interfaces and
answers three questions with real data only:

* which application (namespace) is selected, and was it chosen automatically?
* which approved frozen workflows exist, and does each one apply to the services in that namespace?
* is every piece of evidence a recommendation needs available, and if not, which one is missing and what is the
  exact next action?

Nothing here builds a workflow, a topology or a failure-impact result, and nothing here writes to a cluster. A missing
piece of evidence is reported as missing; it is never replaced, defaulted or estimated, and a request that cannot be
answered is not turned into an empty "all clear". The failure-impact evidence is produced later, when a workflow is
chosen and the recommendation is requested (``live_pipeline``); here it is reported as depending on that choice.

Component 1's workflow discovery, Component 2's topology and Component 3's prediction are called, never changed.
"""
from typing import Any

from app.engine.deployment_topology_modeling.discovery_all import discover_all
from app.engine.workflow_dependency_modeling import service as workflow_service
from app.shared.evidence_errors import EvidenceError

AVAILABLE = "available"
MISSING = "missing"
INCOMPLETE = "incomplete"
PENDING = "pending"

# Where a person obtains a frozen workflow. Component 1 owns this; the text names its steps and invents none.
FREEZE_NEXT_ACTION = (
    "Open Workflow dependencies, assess the workflow's dependencies from real traces, approve the assessment and "
    "freeze its graph (Component 1's freeze step). Then return here and the workflow will be offered automatically."
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


def _frozen_workflows() -> tuple[list[Any], str | None]:
    """Every approved, frozen workflow graph, found through Component 1's public interface.

    Component 1 stores approved assessments and the frozen graph of each workflow. The assessments name the workflows;
    each is then asked for its frozen graph, and one that has none is simply not listed. If the store cannot be read
    at all, that is returned as the reason and nothing is listed.
    """
    try:
        assessments = workflow_service.list_dependency_assessments()
    except Exception:  # the text of a storage error is never shown
        return [], "The approved workflow store could not be read."
    frozen, seen = [], set()
    for assessment in assessments:
        workflow_id = assessment.workflow_id
        if workflow_id in seen:
            continue
        seen.add(workflow_id)
        try:
            frozen.append(workflow_service.get_frozen_workflow_graph(workflow_id))
        except LookupError:
            continue  # approved or assessed, but never frozen: not available to recommend for
        except Exception:
            return [], "The frozen workflow store could not be read."
    return frozen, None


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
    }


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
    frozen, store_problem = _frozen_workflows()
    workflows = [_describe_workflow(item, row) for item in frozen]
    applicable = [w for w in workflows if w["applicable"]]
    if store_problem:
        evidence.append(_item("workflow", "Approved frozen workflow", MISSING, store_problem,
                              "Check that the extension backend and its database are running."))
        actions.append("Check that the extension backend and its database are running.")
    elif not workflows:
        evidence.append(_item("workflow", "Approved frozen workflow", MISSING,
                              "No workflow has been approved and frozen yet, so there is no workflow dependency "
                              "evidence to recommend from.", FREEZE_NEXT_ACTION))
        actions.append(FREEZE_NEXT_ACTION)
    elif row and not applicable:
        evidence.append(_item("workflow", "Approved frozen workflow", MISSING,
                              f"{len(workflows)} frozen workflow(s) exist, but none covers the services in {chosen}.",
                              "Freeze a workflow whose services are in this application: " + FREEZE_NEXT_ACTION))
        actions.append("Freeze a workflow whose services are in this application: " + FREEZE_NEXT_ACTION)
    elif applicable:
        evidence.append(_item("workflow", "Approved frozen workflow", AVAILABLE,
                              f"{len(applicable)} of {len(workflows)} frozen workflow(s) apply to {chosen}."))
    else:
        evidence.append(_item("workflow", "Approved frozen workflow", AVAILABLE,
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

    # 6. failure-impact evidence depends on that choice (Component 3), so it is reported as such, not assumed
    evidence.append(_item(
        "failureImpact", "Failure impact", PENDING if selected else MISSING,
        "Computed by the failure-impact predictor from the frozen workflow and the live topology when the "
        "recommendation is requested." if selected else
        "It is computed from a frozen workflow and the live topology, so it cannot be computed until a workflow "
        "applies to the selected application.",
        None if selected else "Select an application and a frozen workflow that applies to it."))

    ready = bool(cluster["state"] == AVAILABLE and row and selected)
    return {
        "cluster": cluster,
        "namespaces": rows,
        "namespace": chosen,
        "namespaceChoice": choice,
        "workflows": workflows,
        "workflowId": selected,
        "workflowChoice": selection,
        "evidence": evidence,
        "ready": ready,
        "nextActions": actions,
        "readOnly": True,
    }
