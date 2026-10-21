"""Parse research-only captured experiment manifests without file I/O."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from app.engine.failure_impact_prediction.captured_evidence_combiner import (
    combine_captured_experiment_evidence,
)
from app.engine.failure_impact_prediction.captured_experiment import (
    CapturedServiceEvidence,
    CapturedWorkflowEvidence,
    build_captured_service_evidence,
    build_captured_workflow_evidence,
    parse_service_availability_result,
    parse_workflow_execution_result,
)
from app.engine.failure_impact_prediction.validation_evidence import (
    ObservedFailureEvidence,
)


@dataclass(frozen=True)
class CapturedExperimentManifest:
    """Parsed service and workflow captures from one experiment manifest."""

    service_capture: CapturedServiceEvidence
    workflow_capture: CapturedWorkflowEvidence

    @property
    def complete(self) -> bool:
        return (
            self.service_capture.service_observation_complete
            and self.workflow_capture.workflow_observation_complete
        )


_REQUIRED_FIELDS = {
    "experimentId",
    "failedNode",
    "observationSource",
    "observedAt",
    "expectedServices",
    "serviceResults",
    "expectedWorkflows",
    "workflowResults",
}
_OPTIONAL_FIELDS = {
    "workflowFreezeId",
    "deploymentSnapshotId",
    "namespace",
}


def _required_string(payload: Mapping[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_string(
    payload: Mapping[str, object],
    field_name: str,
) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string when provided")
    return value


def _string_array(
    payload: Mapping[str, object],
    field_name: str,
) -> tuple[str, ...]:
    value = payload.get(field_name)
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError(f"{field_name} must be an array of strings")
    return tuple(value)


def _result_mapping(
    payload: Mapping[str, object],
    field_name: str,
) -> dict[str, str]:
    value = payload.get(field_name)
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object of string results")
    if any(
        not isinstance(key, str) or not isinstance(result, str)
        for key, result in value.items()
    ):
        raise ValueError(f"{field_name} must contain string keys and values")
    return dict(value)


def _observed_at(payload: Mapping[str, object]) -> datetime:
    value = _required_string(payload, "observedAt")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("observedAt must be a valid ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observedAt must include timezone information")
    return parsed


def parse_captured_experiment_manifest(
    payload: Mapping[str, object],
) -> CapturedExperimentManifest:
    """Parse one manifest while retaining incomplete probe coverage."""

    if not isinstance(payload, Mapping):
        raise ValueError("captured experiment manifest must be an object")
    missing_fields = tuple(sorted(_REQUIRED_FIELDS - set(payload)))
    if missing_fields:
        raise ValueError(
            "captured experiment manifest is missing fields: "
            + ", ".join(missing_fields)
        )
    unknown_fields = tuple(
        sorted(set(payload) - _REQUIRED_FIELDS - _OPTIONAL_FIELDS)
    )
    if unknown_fields:
        raise ValueError(
            "captured experiment manifest contains unknown fields: "
            + ", ".join(unknown_fields)
        )

    service_results = _result_mapping(payload, "serviceResults")
    workflow_results = _result_mapping(payload, "workflowResults")
    common = {
        "experiment_id": _required_string(payload, "experimentId"),
        "failed_node": _required_string(payload, "failedNode"),
        "observation_source": _required_string(payload, "observationSource"),
        "observed_at": _observed_at(payload),
        "workflow_freeze_id": _optional_string(payload, "workflowFreezeId"),
        "deployment_snapshot_id": _optional_string(
            payload,
            "deploymentSnapshotId",
        ),
        "namespace": _optional_string(payload, "namespace"),
    }
    service_capture = build_captured_service_evidence(
        expected_services=_string_array(payload, "expectedServices"),
        observations=tuple(
            parse_service_availability_result(service, result)
            for service, result in service_results.items()
        ),
        **common,
    )
    workflow_capture = build_captured_workflow_evidence(
        expected_workflows=_string_array(payload, "expectedWorkflows"),
        observations=tuple(
            parse_workflow_execution_result(workflow_id, result)
            for workflow_id, result in workflow_results.items()
        ),
        **common,
    )
    return CapturedExperimentManifest(
        service_capture=service_capture,
        workflow_capture=workflow_capture,
    )


def require_complete_manifest_evidence(
    manifest: CapturedExperimentManifest,
) -> ObservedFailureEvidence:
    """Combine a parsed manifest only when all expected probes are present."""

    return combine_captured_experiment_evidence(
        manifest.service_capture,
        manifest.workflow_capture,
    )


__all__ = [
    "CapturedExperimentManifest",
    "parse_captured_experiment_manifest",
    "require_complete_manifest_evidence",
]
