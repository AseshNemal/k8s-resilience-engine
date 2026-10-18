"""Service-survival rules for a virtual node failure."""

from app.engine.failure_impact_prediction.models import (
    NodeFailureSimulation,
    ServiceSurvivalPrediction,
)


def calculate_service_survival(
    simulation: NodeFailureSimulation,
) -> tuple[ServiceSurvivalPrediction, ...]:
    """A service survives when at least one ready replica remains."""

    predictions: list[ServiceSurvivalPrediction] = []
    for state in simulation.service_states:
        survives = bool(state.surviving_ready_pods)
        predictions.append(
            ServiceSurvivalPrediction(
                service=state.service,
                survives=survives,
                initially_ready_pods=state.initially_ready_pods,
                lost_ready_pods=state.lost_ready_pods,
                surviving_ready_pods=state.surviving_ready_pods,
                caused_by_failed_node=(
                    bool(state.initially_ready_pods)
                    and bool(state.lost_ready_pods)
                    and not survives
                ),
            )
        )
    return tuple(predictions)
