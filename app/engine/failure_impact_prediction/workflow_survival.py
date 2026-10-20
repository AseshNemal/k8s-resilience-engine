"""Workflow-survival rules over mandatory service availability."""

from app.engine.failure_impact_prediction.models import (
    CrossLayerGraph,
    ServiceSurvivalPrediction,
    WorkflowSurvivalPrediction,
)


def calculate_workflow_survival(
    graph: CrossLayerGraph,
    services: tuple[ServiceSurvivalPrediction, ...],
) -> WorkflowSurvivalPrediction:
    """A workflow survives only when every mandatory service survives."""

    predictions = {prediction.service: prediction for prediction in services}
    missing = sorted(set(graph.mandatory_services) - predictions.keys())
    if missing:
        raise ValueError(
            "No service-survival result for mandatory services: "
            + ", ".join(missing)
        )

    failed_due_to = tuple(
        service_name
        for service_name in graph.mandatory_services
        if not predictions[service_name].survives
    )
    return WorkflowSurvivalPrediction(
        workflow_id=graph.workflow_id,
        survives=not failed_due_to,
        failed_due_to=failed_due_to,
    )
