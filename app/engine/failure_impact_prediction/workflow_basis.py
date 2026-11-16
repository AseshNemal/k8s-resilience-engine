"""Which workflows a failure-impact answer is based on.

The page does not ask for a workflow ID. It uses what the connected cluster's application already has:

1. workflows approved and frozen by Component 1 (observed and approved evidence), if any apply;
2. otherwise workflows a person declared or reviewed (a declaration file, or a reviewed editor revision),
   always labelled DECLARED: they are not observed and not approved.

Frozen and declared workflows are never mixed by default (only a frozen workflow can ever reach the production
approval gate); a person can still name any applicable workflows explicitly. A workflow is *applicable* only when
every service it names is fully placed in the topology; otherwise it is listed with the reason and left out, never
guessed. Everything is read through Component 1's public service interface (and Component 4's declaration loader for
the declaration file, so both pages show the same workflows).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.engine.failure_impact_prediction.overview import DECLARED, FROZEN, WorkflowBasis
from app.shared.evidence_errors import EvidenceError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkflowCandidate:
    basis: WorkflowBasis
    applicable: bool
    reason: str | None
    included: bool = False


def _referenced(workflow) -> list[str]:
    names = set(workflow.mandatory_services) | set(workflow.optional_services)
    for edge in workflow.edges:
        names.update((edge.source, edge.target))
    return sorted(names)


def _applicability(workflow, discovery) -> str | None:
    """Why this workflow cannot be evaluated against the discovered topology, or None when it can."""
    placed = {service.name for service in discovery.graph.services}
    missing = [name for name in _referenced(workflow) if name not in placed]
    if missing:
        return "Not found in this application: " + ", ".join(missing) + "."
    incomplete = [name for name in _referenced(workflow)
                  if (evidence := discovery.service_evidence.get(name)) is not None and not evidence.complete]
    if incomplete:
        return "Placement is not fully resolved for: " + ", ".join(incomplete) + "."
    return None


def _frozen_bases() -> tuple[list[WorkflowBasis], str | None]:
    from app.engine.workflow_dependency_modeling import service as workflow_service

    try:
        assessments = workflow_service.list_dependency_assessments()
    except Exception:
        logger.exception("Approved workflow store could not be read")
        return [], "The approved workflow store could not be read."
    bases: list[WorkflowBasis] = []
    seen: set[str] = set()
    for assessment in assessments:
        workflow_id = assessment.workflow_id
        if workflow_id in seen:
            continue
        seen.add(workflow_id)
        try:
            frozen = workflow_service.get_frozen_workflow_graph(workflow_id)
        except LookupError:
            continue
        except Exception:
            logger.exception("Frozen workflow store could not be read")
            return [], "The frozen workflow store could not be read."
        bases.append(WorkflowBasis(frozen.graph.model_copy(deep=True), FROZEN, None, None))
    return bases, None


def _declared_bases(namespace: str) -> tuple[list[WorkflowBasis], str | None]:
    from app.engine.recommendation_evaluation_validation.declared_workflow import load_research_workflows

    try:
        loaded = load_research_workflows()
    except EvidenceError as error:
        return [], error.message
    bases = []
    for item in loaded:
        if item.namespace and item.namespace != namespace:
            continue                                 # a reviewed revision bound to another application is not this one's
        bases.append(WorkflowBasis(item.graph.model_copy(deep=True), DECLARED, item.declared_by, item.note,
                                  getattr(item, "display_name", None)))
    return bases, None


def resolve_workflows(
    discovery,
    namespace: str,
    requested_ids: Sequence[str] = (),
) -> tuple[list[WorkflowCandidate], list[str]]:
    """Every workflow found, with whether it applies and whether it is used. Also the problems met reading them.

    With ``requested_ids`` exactly those (if applicable) are used. Without, frozen applicable workflows are used if
    there are any, else declared applicable ones.
    """
    problems: list[str] = []
    frozen, frozen_problem = _frozen_bases()
    declared, declared_problem = _declared_bases(namespace)
    if frozen_problem:
        problems.append(frozen_problem)
    if declared_problem:
        problems.append(declared_problem)

    candidates: list[WorkflowCandidate] = []
    for basis in [*frozen, *declared]:
        reason = _applicability(basis.workflow, discovery)
        candidates.append(WorkflowCandidate(basis, reason is None, reason))

    wanted = {item for item in requested_ids if item}
    if wanted:
        chosen = {id(c.basis) for c in candidates if c.applicable and c.basis.workflow.workflow_id in wanted}
    else:
        frozen_ok = [c for c in candidates if c.applicable and c.basis.kind == FROZEN]
        pool = frozen_ok or [c for c in candidates if c.applicable and c.basis.kind == DECLARED]
        chosen = {id(c.basis) for c in pool}
    return [WorkflowCandidate(c.basis, c.applicable, c.reason, id(c.basis) in chosen) for c in candidates], problems


def describe_candidates(candidates: Sequence[WorkflowCandidate]) -> list[dict[str, Any]]:
    return [{
        "workflowId": c.basis.workflow.workflow_id,
        "name": c.basis.display_name,
        "kind": c.basis.kind,
        "criticality": c.basis.workflow.criticality,
        "mandatoryServices": list(c.basis.workflow.mandatory_services),
        "declaredBy": c.basis.declared_by,
        "applicable": c.applicable,
        "included": c.included,
        "reason": c.reason,
    } for c in candidates]


def basis_label(candidates: Sequence[WorkflowCandidate]) -> dict[str, Any]:
    used = [c for c in candidates if c.included]
    kinds = {c.basis.kind for c in used}
    if not used:
        return {"kind": "none", "text": "No workflow applies, so only service-level impact is shown. "
                                        "Workflow survival and blast radius are unknown."}
    if kinds == {FROZEN}:
        return {"kind": FROZEN, "text": f"Based on {len(used)} approved, frozen workflow(s)."}
    if kinds == {DECLARED}:
        return {"kind": DECLARED, "text": f"Based on {len(used)} declared workflow(s). They are declared by a person, "
                                           "not observed from traces and not approved, so workflow results are conditional on them."}
    return {"kind": "mixed", "text": f"Based on {len(used)} workflows, frozen and declared together. Declared ones are not observed."}


__all__ = ["WorkflowCandidate", "basis_label", "describe_candidates", "resolve_workflows"]
