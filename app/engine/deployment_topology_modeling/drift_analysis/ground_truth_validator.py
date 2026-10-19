from app.engine.deployment_topology_modeling.placement_metrics import ExposureStatus
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    TARGET_MAPPING_ACCURACY,
    TARGET_CONCENTRATION_ACCURACY,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    ModelAccuracyCertificate,
    ServiceExposureResult,
)


def _map_accuracy(predicted: dict[str, str], truth: dict[str, str]) -> float | None:
    """Share of keys whose predicted value equals the ground truth.

    The denominator is the union of keys, so a missing prediction and an invented extra
    prediction both count as errors.
    """
    keys = set(truth) | set(predicted)
    if not keys:
        return None
    return sum(predicted.get(k) == truth.get(k) for k in keys) / len(keys)


def _set_accuracy(predicted: set[str], truth: set[str], evaluated: set[str]) -> float | None:
    if not evaluated:
        return None
    return sum((s in predicted) == (s in truth) for s in evaluated) / len(evaluated)


class GroundTruthValidator:
    """Accuracy of the topology model against labelled ground truth (Study Guide sections 11-12).

    Ground truth is manual kubectl inspection: `kubectl get pods -o wide` for pod->node,
    Service selectors / endpoints for service->pod, ownerReferences for owner resolution, and
    hand-labelled concentrated / exposed services.
    """

    @staticmethod
    def evaluate_mapping_accuracies(
        predicted_pod_service_map: dict[str, str],
        ground_truth_pod_service_map: dict[str, str],
        predicted_pod_node_map: dict[str, str],
        ground_truth_pod_node_map: dict[str, str],
        predicted_owner_map: dict[str, str],
        ground_truth_owner_map: dict[str, str],
        predicted_exposures: list[ServiceExposureResult],
        ground_truth_exposed_services: set[str],
        ground_truth_concentrated_services: set[str] | None = None,
    ) -> ModelAccuracyCertificate:
        """With the node as failure domain, concentrated and exposed are the same condition, so
        concentrated ground truth defaults to the exposed labels unless given separately."""
        svc_pod_acc = _map_accuracy(predicted_pod_service_map, ground_truth_pod_service_map)
        pod_node_acc = _map_accuracy(predicted_pod_node_map, ground_truth_pod_node_map)
        owner_acc = _map_accuracy(predicted_owner_map, ground_truth_owner_map)

        if ground_truth_concentrated_services is None:
            ground_truth_concentrated_services = ground_truth_exposed_services

        evaluated = {e.service_name for e in predicted_exposures}
        predicted_exposed = {
            e.service_name for e in predicted_exposures if e.exposure_status == ExposureStatus.EXPOSED
        }
        predicted_concentrated = {e.service_name for e in predicted_exposures if e.concentrated}
        exposure_acc = _set_accuracy(
            predicted_exposed, ground_truth_exposed_services, evaluated | ground_truth_exposed_services
        )
        conc_acc = _set_accuracy(
            predicted_concentrated, ground_truth_concentrated_services,
            evaluated | ground_truth_concentrated_services,
        )

        measured = (svc_pod_acc, pod_node_acc, owner_acc, conc_acc)
        is_sound = (
            None not in measured
            and min(svc_pod_acc, pod_node_acc, owner_acc) >= TARGET_MAPPING_ACCURACY
            and conc_acc >= TARGET_CONCENTRATION_ACCURACY
        )

        def r(value: float | None) -> float | None:
            return round(value, 4) if value is not None else None

        return ModelAccuracyCertificate(
            service_pod_mapping_accuracy=r(svc_pod_acc),
            pod_node_mapping_accuracy=r(pod_node_acc),
            owner_resolution_accuracy=r(owner_acc),
            concentration_detection_accuracy=r(conc_acc),
            exposure_detection_accuracy=r(exposure_acc),
            is_statistically_sound=is_sound,
        )
