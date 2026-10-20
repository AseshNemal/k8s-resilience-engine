"""Validated runtime input handoff from Components 1 and 2.

Everything here is real evidence or an explicit error. Component 2's live discovery either returns the
connected cluster's topology or raises; there is no offline fallback. A topology that is only partly
resolved is never treated as verified: if any service the workflow depends on has unresolved or missing
placement, the prediction is refused with ``TOPOLOGY_INCOMPLETE`` rather than computed on a guess.
"""
from collections.abc import Sequence

import logging
from dataclasses import dataclass, field
from typing import Any

from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.workflow import WorkflowGraph

logger = logging.getLogger(__name__)

COMPONENT = "failure_impact"


class RuntimeInputUnavailableError(EvidenceError):
    """Raised when an owning component cannot provide its runtime contract."""

    def __init__(self, message: str, *, code: str = "RUNTIME_INPUT_UNAVAILABLE") -> None:
        super().__init__(code, message, http_status=503, component=COMPONENT, retryable=True)


class WorkflowEvidenceUnavailableError(EvidenceError, LookupError):
    """No frozen (approved) workflow graph exists for the requested workflow."""

    def __init__(self, message: str) -> None:
        super().__init__("WORKFLOW_EVIDENCE_UNAVAILABLE", message, http_status=404, component=COMPONENT)

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class RuntimeComponentInputs:
    """Component-owned inputs and provenance used for one live prediction."""

    workflow: WorkflowGraph
    deployment: DeploymentGraph
    workflow_freeze_id: str
    workflow_assessment_id: str
    namespace: str
    topology_status: str = "ok"
    topology_captured_at: str = ""
    topology_warnings: list[Any] = field(default_factory=list)
    topology_unresolved: list[Any] = field(default_factory=list)


def _referenced_services(workflow: WorkflowGraph) -> list[str]:
    names = set(workflow.mandatory_services) | set(workflow.optional_services)
    for edge in workflow.edges:
        names.update((edge.source, edge.target))
    return sorted(names)


def _require_sufficient_evidence(workflow: WorkflowGraph, discovery: Any) -> None:
    """Refuse when unresolved placement could change the answer for a service this workflow uses."""

    placed = {service.name for service in discovery.graph.services}
    blocked: dict[str, list[str]] = {}
    for name in _referenced_services(workflow):
        evidence = discovery.service_evidence.get(name)
        if name not in placed:
            blocked[name] = list(evidence.reasons) if evidence and evidence.reasons else (
                ["NO_PLACEMENT_OBSERVED"] if evidence else ["SERVICE_NOT_DISCOVERED"]
            )
        elif evidence is not None and not evidence.complete:
            blocked[name] = list(evidence.reasons)

    if blocked:
        relevant = [
            item.model_dump(by_alias=True)
            for item in discovery.unresolved
            if set(item.affects) & blocked.keys()
        ]
        raise EvidenceError(
            "TOPOLOGY_INCOMPLETE",
            "The topology is not complete enough for the services this workflow depends on, so no "
            "prediction was made.",
            http_status=409,
            component=COMPONENT,
            details={
                "services": [{"service": name, "reasons": reasons} for name, reasons in sorted(blocked.items())],
                "unresolved": relevant,
            },
        )


@dataclass(frozen=True)
class WorkflowInputProvenance:
    """Identity of one frozen Component 1 graph used in a prediction."""

    workflow_id: str
    freeze_id: str
    assessment_id: str


@dataclass(frozen=True)
class RuntimePortfolioInputs:
    """Several frozen workflows joined to one deployment snapshot."""

    workflows: tuple[WorkflowGraph, ...]
    deployment: DeploymentGraph
    workflow_provenance: tuple[WorkflowInputProvenance, ...]
    namespace: str


def _load_frozen_workflow(workflow_id: str):
    from app.engine.workflow_dependency_modeling.service import (
        get_frozen_workflow_graph,
    )

    try:
        return get_frozen_workflow_graph(workflow_id)
    except LookupError as error:
        # No approved, frozen workflow graph: an explicit 404-style evidence error, never a guess.
        raise WorkflowEvidenceUnavailableError(str(error)) from error
    except Exception as error:
        logger.exception("Frozen workflow storage failed")
        raise RuntimeInputUnavailableError(
            "Component 1 frozen-workflow storage is unavailable", code="WORKFLOW_STORAGE_UNAVAILABLE"
        ) from error


def _load_deployment(namespace: str) -> DeploymentGraph:
    """The live topology graph alone (used by the portfolio path)."""
    from app.engine.deployment_topology_modeling.service import (
        get_live_deployment_graph,
    )

    try:
        return get_live_deployment_graph(namespace=namespace)
    except Exception as error:
        raise RuntimeInputUnavailableError(
            "Component 2 deployment topology is unavailable"
        ) from error


def _discover_topology(namespace: str):
    """Live discovery with its status, warnings and unresolved items (used by the single-workflow path).

    Unlike _load_deployment this keeps the evidence about how complete the topology is, so the caller can
    refuse to predict on a partly resolved one. An EvidenceError from the cluster layer (not connected,
    credential rejected, ...) passes through unchanged; anything unexpected becomes TOPOLOGY_UNAVAILABLE
    without leaking its text.
    """
    from app.engine.deployment_topology_modeling.service import discover_live

    try:
        return discover_live(namespace=namespace)
    except EvidenceError:
        raise
    except Exception as error:
        logger.exception("Topology discovery failed unexpectedly")
        raise RuntimeInputUnavailableError(
            "Component 2 deployment topology is unavailable", code="TOPOLOGY_UNAVAILABLE"
        ) from error


def get_runtime_component_inputs(
    workflow_id: str,
    namespace: str = "default",
) -> RuntimeComponentInputs:
    """Read a frozen workflow and current topology via public service interfaces.

    Component 3 deliberately requires Component 1's frozen graph rather than a candidate or unapproved
    graph. Component 2 supplies the live topology of the connected cluster or raises.
    """

    normalized_workflow_id = workflow_id.strip()
    if not normalized_workflow_id:
        raise ValueError("workflow_id must be a non-empty string")

    normalized_namespace = namespace.strip()
    if not normalized_namespace:
        raise ValueError("namespace must be a non-empty string")

    frozen_workflow = _load_frozen_workflow(normalized_workflow_id)
    discovery = _discover_topology(normalized_namespace)

    workflow = frozen_workflow.graph.model_copy(deep=True)
    _require_sufficient_evidence(workflow, discovery)

    return RuntimeComponentInputs(
        workflow=workflow,
        deployment=discovery.graph.model_copy(deep=True),
        workflow_freeze_id=frozen_workflow.freeze_id,
        workflow_assessment_id=frozen_workflow.assessment_id,
        namespace=normalized_namespace,
        topology_status=discovery.status,
        topology_captured_at=discovery.captured_at,
        topology_warnings=list(discovery.warnings),
        topology_unresolved=list(discovery.unresolved),
    )


def get_runtime_portfolio_inputs(
    workflow_ids: Sequence[str],
    namespace: str = "default",
) -> RuntimePortfolioInputs:
    """Load several frozen workflows and collect their topology only once."""

    if not workflow_ids:
        raise ValueError("At least one workflow_id is required")

    normalized_workflow_ids = tuple(
        workflow_id.strip() for workflow_id in workflow_ids
    )
    if any(not workflow_id for workflow_id in normalized_workflow_ids):
        raise ValueError("workflow_ids must contain non-empty strings")

    duplicate_ids = sorted(
        {
            workflow_id
            for workflow_id in normalized_workflow_ids
            if normalized_workflow_ids.count(workflow_id) > 1
        }
    )
    if duplicate_ids:
        raise ValueError(
            "Duplicate workflow IDs: " + ", ".join(duplicate_ids)
        )

    normalized_namespace = namespace.strip()
    if not normalized_namespace:
        raise ValueError("namespace must be a non-empty string")

    frozen_workflows = tuple(
        _load_frozen_workflow(workflow_id)
        for workflow_id in normalized_workflow_ids
    )
    deployment = _load_deployment(normalized_namespace)

    return RuntimePortfolioInputs(
        workflows=tuple(
            frozen_workflow.graph.model_copy(deep=True)
            for frozen_workflow in frozen_workflows
        ),
        deployment=deployment.model_copy(deep=True),
        workflow_provenance=tuple(
            WorkflowInputProvenance(
                workflow_id=workflow_id,
                freeze_id=frozen_workflow.freeze_id,
                assessment_id=frozen_workflow.assessment_id,
            )
            for workflow_id, frozen_workflow in zip(
                normalized_workflow_ids,
                frozen_workflows,
                strict=True,
            )
        ),
        namespace=normalized_namespace,
    )


__all__ = [
    "RuntimeComponentInputs",
    "RuntimeInputUnavailableError",
    "RuntimePortfolioInputs",
    "WorkflowEvidenceUnavailableError",
    "WorkflowInputProvenance",
    "get_runtime_component_inputs",
    "get_runtime_portfolio_inputs",
]
