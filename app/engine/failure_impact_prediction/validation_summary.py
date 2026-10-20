"""Compose Component 3 validation records into one research summary."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.engine.failure_impact_prediction.prediction_validation import (
    PredictionValidationRecord,
)
from app.engine.failure_impact_prediction.validation_batch import (
    PredictionValidationBatch,
    build_prediction_validation_batch,
)
from app.engine.failure_impact_prediction.validation_metrics import (
    ValidationBatchMetrics,
    calculate_validation_batch_metrics,
)


@dataclass(frozen=True)
class PredictionValidationSummary:
    """One immutable result containing evidence and aggregate metrics."""

    batch: PredictionValidationBatch
    metrics: ValidationBatchMetrics


def build_prediction_validation_summary(
    batch_id: str,
    records: Sequence[PredictionValidationRecord],
) -> PredictionValidationSummary:
    """Build a traceable repeated-experiment summary in one operation."""

    batch = build_prediction_validation_batch(batch_id, records)
    metrics = calculate_validation_batch_metrics(batch)
    return PredictionValidationSummary(batch=batch, metrics=metrics)


__all__ = [
    "PredictionValidationSummary",
    "build_prediction_validation_summary",
]
