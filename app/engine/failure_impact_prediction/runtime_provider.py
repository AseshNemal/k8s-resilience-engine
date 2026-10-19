"""Validated runtime input handoff from Components 1 and 2."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.workflow import WorkflowGraph


class RuntimeInputUnavailableError(RuntimeError):
    """Raised when an owning component cannot provide its runtime contract."""


@dataclass(frozen=True)
class RuntimeComponentInputs:
    """Component-owned inputs and provenance used for one live prediction."""

    workflow: WorkflowGraph
    deployment: DeploymentGraph
    workflow_freeze_id: str
    workflow_assessment_id: str
    namespace: str


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
    except LookupError:
        raise
    except Exception as error:
        raise RuntimeInputUnavailableError(
            "Component 1 frozen-workflow storage is unavailable"
        ) from error


def _load_deployment(namespace: str) -> DeploymentGraph:
    from app.engine.deployment_topology_modeling.service import (
        get_live_deployment_graph,
    )

    try:
        return get_live_deployment_graph(namespace=namespace)
    except Exception as error:
        raise RuntimeInputUnavailableError(
            "Component 2 deployment topology is unavailable"
        ) from error


def get_runtime_component_inputs(
    workflow_id: str,
    namespace: str = "default",
) -> RuntimeComponentInputs:
    """Read a frozen workflow and current topology via public service interfaces.

    Component 3 deliberately requires Component 1's frozen graph rather than a
    candidate or unapproved graph. Component 2 remains responsible for live
    cluster collection and its documented offline fallback behavior.
    """

    normalized_workflow_id = workflow_id.strip()
    if not normalized_workflow_id:
        raise ValueError("workflow_id must be a non-empty string")

    normalized_namespace = namespace.strip()
    if not normalized_namespace:
        raise ValueError("namespace must be a non-empty string")

    frozen_workflow = _load_frozen_workflow(normalized_workflow_id)
    deployment = _load_deployment(normalized_namespace)

    return RuntimeComponentInputs(
        workflow=frozen_workflow.graph.model_copy(deep=True),
        deployment=deployment.model_copy(deep=True),
        workflow_freeze_id=frozen_workflow.freeze_id,
        workflow_assessment_id=frozen_workflow.assessment_id,
        namespace=normalized_namespace,
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
    "WorkflowInputProvenance",
    "get_runtime_component_inputs",
    "get_runtime_portfolio_inputs",
]
