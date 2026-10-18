"""Validated runtime input handoff from Components 1 and 2."""

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

    from app.engine.deployment_topology_modeling.service import (
        get_live_deployment_graph,
    )
    from app.engine.workflow_dependency_modeling.service import (
        get_frozen_workflow_graph,
    )

    try:
        frozen_workflow = get_frozen_workflow_graph(normalized_workflow_id)
    except LookupError:
        raise
    except Exception as error:
        raise RuntimeInputUnavailableError(
            "Component 1 frozen-workflow storage is unavailable"
        ) from error

    try:
        deployment = get_live_deployment_graph(namespace=normalized_namespace)
    except Exception as error:
        raise RuntimeInputUnavailableError(
            "Component 2 deployment topology is unavailable"
        ) from error

    return RuntimeComponentInputs(
        workflow=frozen_workflow.graph.model_copy(deep=True),
        deployment=deployment.model_copy(deep=True),
        workflow_freeze_id=frozen_workflow.freeze_id,
        workflow_assessment_id=frozen_workflow.assessment_id,
        namespace=normalized_namespace,
    )


__all__ = [
    "RuntimeComponentInputs",
    "RuntimeInputUnavailableError",
    "get_runtime_component_inputs",
]
