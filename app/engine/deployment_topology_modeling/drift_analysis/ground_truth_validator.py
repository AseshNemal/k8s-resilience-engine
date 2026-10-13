from typing import Any
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    TARGET_MAPPING_ACCURACY,
    TARGET_CONCENTRATION_ACCURACY,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    ModelAccuracyCertificate,
    ServiceExposureResult,
)


class GroundTruthValidator:
    """
    Evaluates topological modeling accuracy against controlled synthetic ground-truth.
    
    Computes the 4 mandatory thesis metrics:
    1. Service-to-Pod mapping accuracy
    2. Pod-to-Node placement accuracy
    3. Controller owner-reference resolution accuracy
    4. Failure-domain concentration detection accuracy
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
    ) -> ModelAccuracyCertificate:
        # 1. Service-to-Pod Mapping Accuracy
        total_pods = len(ground_truth_pod_service_map)
        correct_svc_pods = (
            sum(
                1 for pod, svc in ground_truth_pod_service_map.items()
                if predicted_pod_service_map.get(pod) == svc
            )
            if total_pods > 0
            else 0
        )
        svc_pod_acc = correct_svc_pods / total_pods if total_pods > 0 else 1.0

        # 2. Pod-to-Node Mapping Accuracy
        total_host_pods = len(ground_truth_pod_node_map)
        correct_node_pods = (
            sum(
                1 for pod, node in ground_truth_pod_node_map.items()
                if predicted_pod_node_map.get(pod) == node
            )
            if total_host_pods > 0
            else 0
        )
        pod_node_acc = correct_node_pods / total_host_pods if total_host_pods > 0 else 1.0

        # 3. Owner Resolution Accuracy
        total_owners = len(ground_truth_owner_map)
        correct_owners = (
            sum(
                1 for child, parent in ground_truth_owner_map.items()
                if predicted_owner_map.get(child) == parent
            )
            if total_owners > 0
            else 0
        )
        owner_acc = correct_owners / total_owners if total_owners > 0 else 1.0

        # 4. Concentration / Exposure Detection Accuracy
        total_evaluated_services = len(predicted_exposures)
        if total_evaluated_services > 0:
            detected_exposed = {
                exp.service_name
                for exp in predicted_exposures
                if len(exp.exposed_failure_domains) > 0 or exp.ready_replicas == 1
            }
            # Calculate agreement: (True Positives + True Negatives) / Total
            tp = len(detected_exposed.intersection(ground_truth_exposed_services))
            tn = len(
                {exp.service_name for exp in predicted_exposures}
                - detected_exposed
                - ground_truth_exposed_services
            )
            conc_acc = (tp + tn) / total_evaluated_services
        else:
            conc_acc = 1.0

        is_sound = (
            svc_pod_acc >= TARGET_MAPPING_ACCURACY
            and pod_node_acc >= TARGET_MAPPING_ACCURACY
            and owner_acc >= TARGET_MAPPING_ACCURACY
            and conc_acc >= TARGET_CONCENTRATION_ACCURACY
        )

        return ModelAccuracyCertificate(
            service_pod_mapping_accuracy=round(svc_pod_acc, 4),
            pod_node_mapping_accuracy=round(pod_node_acc, 4),
            owner_resolution_accuracy=round(owner_acc, 4),
            concentration_detection_accuracy=round(conc_acc, 4),
            is_statistically_sound=is_sound,
        )
