"""Immutable reference and evaluation artifacts for Phase 5."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    ValidatedDependencyType,
)
from app.engine.workflow_dependency_modeling.models import ComponentDataError


EVALUATED_LABELS = (
    ValidatedDependencyType.MANDATORY,
    ValidatedDependencyType.OPTIONAL,
    ValidatedDependencyType.SUPPORTING,
)


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ComponentDataError(f"Classification artifact requires non-empty '{key}'")
    return value.strip()


def _version(data: Mapping[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ComponentDataError(f"{key} must be a positive integer")
    return value


def _timestamp(value: Any, key: str) -> datetime:
    if not isinstance(value, str):
        raise ComponentDataError(f"{key} must be ISO 8601")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ComponentDataError(f"{key} must be ISO 8601") from err
    if result.tzinfo is None:
        raise ComponentDataError(f"{key} must include a timezone")
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class DependencyReferenceLabel:
    service_name: str
    classification: ValidatedDependencyType
    justification: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DependencyReferenceLabel":
        try:
            classification = ValidatedDependencyType(data.get("classification"))
        except ValueError as err:
            raise ComponentDataError("Reference classification is invalid") from err
        if classification not in EVALUATED_LABELS:
            raise ComponentDataError(
                "References may only label mandatory, optional, or supporting"
            )
        return cls(
            service_name=_text(data, "serviceName"),
            classification=classification,
            justification=_text(data, "justification"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "serviceName": self.service_name,
            "classification": self.classification.value,
            "justification": self.justification,
        }


@dataclass(frozen=True)
class DependencyClassificationReference:
    reference_id: str
    workflow_id: str
    scenario_version: int
    reference_version: int
    labels: tuple[DependencyReferenceLabel, ...]
    source: str
    synthetic: bool
    created_at: datetime
    created_by: str

    @classmethod
    def from_mapping(
        cls, data: Mapping[str, Any]
    ) -> "DependencyClassificationReference":
        raw_labels = data.get("labels")
        if not isinstance(raw_labels, list) or not raw_labels:
            raise ComponentDataError("Reference labels must be a non-empty list")
        labels = tuple(DependencyReferenceLabel.from_mapping(item) for item in raw_labels)
        names = [item.service_name for item in labels]
        if len(names) != len(set(names)):
            raise ComponentDataError("Reference labels contain duplicate services")
        synthetic = data.get("synthetic")
        if not isinstance(synthetic, bool):
            raise ComponentDataError("Reference synthetic flag must be boolean")
        return cls(
            reference_id=_text(data, "referenceId"),
            workflow_id=_text(data, "workflowId"),
            scenario_version=_version(data, "scenarioVersion"),
            reference_version=_version(data, "referenceVersion"),
            labels=labels,
            source=_text(data, "source"),
            synthetic=synthetic,
            created_at=_timestamp(data.get("createdAt"), "createdAt"),
            created_by=_text(data, "createdBy"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "referenceId": self.reference_id,
            "workflowId": self.workflow_id,
            "scenarioVersion": self.scenario_version,
            "referenceVersion": self.reference_version,
            "labels": [item.to_mapping() for item in self.labels],
            "source": self.source,
            "synthetic": self.synthetic,
            "createdAt": _iso(self.created_at),
            "createdBy": self.created_by,
        }


@dataclass(frozen=True)
class ClassificationMetrics:
    precision: float
    recall: float
    f1: float
    support: int
    true_positives: int
    false_positives: int
    false_negatives: int

    def to_mapping(self) -> dict[str, Any]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "support": self.support,
            "truePositives": self.true_positives,
            "falsePositives": self.false_positives,
            "falseNegatives": self.false_negatives,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ClassificationMetrics":
        return cls(
            precision=float(data.get("precision", 0.0)),
            recall=float(data.get("recall", 0.0)),
            f1=float(data.get("f1", 0.0)),
            support=int(data.get("support", 0)),
            true_positives=int(data.get("truePositives", 0)),
            false_positives=int(data.get("falsePositives", 0)),
            false_negatives=int(data.get("falseNegatives", 0)),
        )


@dataclass(frozen=True)
class ClassificationEvaluation:
    evaluation_id: str
    workflow_id: str
    scenario_version: int
    environment_id: str
    assessment_ids: tuple[str, ...]
    reference_id: str
    reference_version: int
    synthetic: bool
    confusion_matrix: dict[str, dict[str, int]]
    per_class: dict[str, ClassificationMetrics]
    accuracy: float
    classified_count: int
    expected_count: int
    macro_f1: float
    micro_f1: float
    coverage: float
    abstention_rate: float
    misclassified_services: tuple[str, ...]
    abstained_services: tuple[str, ...]
    missing_services: tuple[str, ...]
    unexpected_services: tuple[str, ...]
    created_at: datetime
    created_by: str

    @property
    def selective_accuracy(self) -> float:
        """Accuracy over classified reference services, excluding abstentions."""
        return self.accuracy

    def to_mapping(self) -> dict[str, Any]:
        return {
            "evaluationId": self.evaluation_id,
            "workflowId": self.workflow_id,
            "scenarioVersion": self.scenario_version,
            "environmentId": self.environment_id,
            "assessmentIds": list(self.assessment_ids),
            "referenceId": self.reference_id,
            "referenceVersion": self.reference_version,
            "synthetic": self.synthetic,
            "confusionMatrix": self.confusion_matrix,
            "perClass": {
                label: metrics.to_mapping()
                for label, metrics in self.per_class.items()
            },
            "accuracy": self.accuracy,
            "selectiveAccuracy": self.selective_accuracy,
            "classifiedCount": self.classified_count,
            "expectedCount": self.expected_count,
            "macroF1": self.macro_f1,
            "microF1": self.micro_f1,
            "coverage": self.coverage,
            "abstentionRate": self.abstention_rate,
            "misclassifiedServices": list(self.misclassified_services),
            "abstainedServices": list(self.abstained_services),
            "missingServices": list(self.missing_services),
            "unexpectedServices": list(self.unexpected_services),
            "createdAt": _iso(self.created_at),
            "createdBy": self.created_by,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ClassificationEvaluation":
        return cls(
            evaluation_id=_text(data, "evaluationId"),
            workflow_id=_text(data, "workflowId"),
            scenario_version=_version(data, "scenarioVersion"),
            environment_id=_text(data, "environmentId"),
            assessment_ids=tuple(str(item) for item in data.get("assessmentIds", [])),
            reference_id=_text(data, "referenceId"),
            reference_version=_version(data, "referenceVersion"),
            synthetic=data.get("synthetic") is True,
            confusion_matrix={
                str(actual): {str(predicted): int(count) for predicted, count in row.items()}
                for actual, row in data.get("confusionMatrix", {}).items()
            },
            per_class={
                str(label): ClassificationMetrics.from_mapping(metrics)
                for label, metrics in data.get("perClass", {}).items()
            },
            accuracy=float(data.get("selectiveAccuracy", data.get("accuracy", 0.0))),
            classified_count=int(data.get("classifiedCount", 0)),
            expected_count=int(data.get("expectedCount", 0)),
            macro_f1=float(data.get("macroF1", 0.0)),
            micro_f1=float(data.get("microF1", 0.0)),
            coverage=float(data.get("coverage", 0.0)),
            abstention_rate=float(data.get("abstentionRate", 0.0)),
            misclassified_services=tuple(data.get("misclassifiedServices", [])),
            abstained_services=tuple(data.get("abstainedServices", [])),
            missing_services=tuple(data.get("missingServices", [])),
            unexpected_services=tuple(data.get("unexpectedServices", [])),
            created_at=_timestamp(data.get("createdAt"), "createdAt"),
            created_by=_text(data, "createdBy"),
        )
