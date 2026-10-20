"""Group Component 3 validation records from repeated experiments."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.engine.failure_impact_prediction.prediction_validation import (
    PredictionValidationRecord,
)


@dataclass(frozen=True)
class PredictionValidationBatch:
    """Immutable grouping of validation records without aggregate metrics."""

    batch_id: str
    records: tuple[PredictionValidationRecord, ...]
    exact_match_experiment_ids: tuple[str, ...]
    service_mismatch_experiment_ids: tuple[str, ...]
    workflow_mismatch_experiment_ids: tuple[str, ...]

    @property
    def total_experiments(self) -> int:
        return len(self.records)


def build_prediction_validation_batch(
    batch_id: str,
    records: Sequence[PredictionValidationRecord],
) -> PredictionValidationBatch:
    """Validate and group repeated experiment records in execution order."""

    normalized_batch_id = batch_id.strip()
    if not normalized_batch_id:
        raise ValueError("batch_id must be a non-empty string")
    if not records:
        raise ValueError("At least one validation record is required")

    immutable_records = tuple(records)
    experiment_ids = tuple(record.experiment_id for record in immutable_records)
    duplicate_ids = tuple(
        sorted(
            {
                experiment_id
                for experiment_id in experiment_ids
                if experiment_ids.count(experiment_id) > 1
            }
        )
    )
    if duplicate_ids:
        raise ValueError(
            "Duplicate validation experiment IDs: "
            + ", ".join(duplicate_ids)
        )

    return PredictionValidationBatch(
        batch_id=normalized_batch_id,
        records=immutable_records,
        exact_match_experiment_ids=tuple(
            record.experiment_id
            for record in immutable_records
            if record.comparison.exact_match
        ),
        service_mismatch_experiment_ids=tuple(
            record.experiment_id
            for record in immutable_records
            if not record.comparison.services_match
        ),
        workflow_mismatch_experiment_ids=tuple(
            record.experiment_id
            for record in immutable_records
            if not record.comparison.workflows_match
        ),
    )


__all__ = [
    "PredictionValidationBatch",
    "build_prediction_validation_batch",
]
