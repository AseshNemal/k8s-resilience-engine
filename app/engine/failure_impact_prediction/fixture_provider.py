"""Input providers for current upstream samples and the Component 3 demo."""

from app.shared.schemas.deployment import (
    DeploymentGraph,
    PodModel,
    ServicePlacement,
)
from app.shared.schemas.workflow import WorkflowGraph


def get_current_component_inputs() -> tuple[WorkflowGraph, DeploymentGraph]:
    """Read the current public sample outputs from Components 1 and 2."""

    # Imports stay local so the pure Component 3 algorithms do not depend on
    # Jaeger or Kubernetes integration modules at import time.
    from app.engine.deployment_topology_modeling.service import (
        get_sample_deployment,
    )
    from app.engine.workflow_dependency_modeling.service import (
        get_sample_workflow,
    )

    return get_sample_workflow(), get_sample_deployment()


def get_demo_inputs() -> tuple[WorkflowGraph, DeploymentGraph]:
    """Complete current upstream samples with disclosed synthetic placements.

    Component 2's present sample includes only ``paymentservice``. The returned
    deployment is a deep copy supplemented with the remaining workflow services
    so Component 3 can demonstrate a complete cross-layer simulation. The
    original Component 2 output is never modified.
    """

    workflow, current_deployment = get_current_component_inputs()
    deployment = current_deployment.model_copy(deep=True)
    existing_services = {service.name for service in deployment.services}

    synthetic_services = [
        ServicePlacement(
            name="frontend",
            declared_replicas=2,
            ready_replicas=2,
            replica_concentration=0.5,
            pods=[
                PodModel(
                    name="frontend-1",
                    node="node-2",
                    zone="zone-a",
                    ready=True,
                ),
                PodModel(
                    name="frontend-2",
                    node="node-3",
                    zone="zone-b",
                    ready=True,
                ),
            ],
        ),
        ServicePlacement(
            name="cartservice",
            declared_replicas=2,
            ready_replicas=2,
            replica_concentration=1.0,
            pods=[
                PodModel(
                    name="cart-1",
                    node="node-1",
                    zone="zone-a",
                    ready=True,
                ),
                PodModel(
                    name="cart-2",
                    node="node-1",
                    zone="zone-a",
                    ready=True,
                ),
            ],
        ),
        ServicePlacement(
            name="checkoutservice",
            declared_replicas=2,
            ready_replicas=2,
            replica_concentration=0.5,
            pods=[
                PodModel(
                    name="checkout-1",
                    node="node-2",
                    zone="zone-a",
                    ready=True,
                ),
                PodModel(
                    name="checkout-2",
                    node="node-3",
                    zone="zone-b",
                    ready=True,
                ),
            ],
        ),
        ServicePlacement(
            name="emailservice",
            declared_replicas=1,
            ready_replicas=1,
            replica_concentration=1.0,
            pods=[
                PodModel(
                    name="email-1",
                    node="node-3",
                    zone="zone-b",
                    ready=True,
                )
            ],
        ),
    ]
    deployment.services.extend(
        service
        for service in synthetic_services
        if service.name not in existing_services
    )
    deployment.snapshot_id = "component3-demo-snapshot-v1"
    return workflow, deployment


def get_demo_deployment() -> DeploymentGraph:
    """Return the complete, clearly identified Component 3 demo topology."""

    _, deployment = get_demo_inputs()
    return deployment
