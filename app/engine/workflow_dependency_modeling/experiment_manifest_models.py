"""Immutable, integrity-checkable manifests for reproducible experiments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.models import ComponentDataError


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ComponentDataError(f"Experiment manifest requires non-empty '{key}'")
    return value.strip()


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ComponentDataError("Experiment manifest createdAt must be ISO 8601")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ComponentDataError(
            "Experiment manifest createdAt must be ISO 8601"
        ) from err
    if result.tzinfo is None:
        raise ComponentDataError(
            "Experiment manifest createdAt must include a timezone"
        )
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ExperimentManifest:
    """A complete snapshot of the inputs and controls for one experiment."""

    manifest_id: str
    schema_version: int
    campaign_id: str
    workflow_id: str
    analysis_session_id: str
    algorithm_version: str
    random_seed: int
    degradation_rates: tuple[float, ...]
    repetitions_per_condition: int
    campaign_snapshot: dict[str, Any]
    analysis_snapshot: dict[str, Any]
    source_digests: dict[str, str]
    content_digest: str
    created_at: datetime
    created_by: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ExperimentManifest":
        schema_version = data.get("schemaVersion")
        if schema_version != 1:
            raise ComponentDataError("Experiment manifest schemaVersion must be 1")
        random_seed = data.get("randomSeed")
        if (
            isinstance(random_seed, bool)
            or not isinstance(random_seed, int)
            or not 0 <= random_seed <= 4_294_967_295
        ):
            raise ComponentDataError("Experiment manifest randomSeed is invalid")
        degradation = data.get("degradationPlan")
        if not isinstance(degradation, Mapping):
            raise ComponentDataError("Experiment manifest degradationPlan is required")
        rates = tuple(float(value) for value in degradation.get("rates", []))
        if not rates or any(value < 0 or value > 1 for value in rates):
            raise ComponentDataError("Degradation rates must be between 0 and 1")
        if tuple(sorted(set(rates))) != rates:
            raise ComponentDataError("Degradation rates must be unique and ascending")
        repetitions = degradation.get("repetitionsPerCondition")
        if (
            isinstance(repetitions, bool)
            or not isinstance(repetitions, int)
            or not 1 <= repetitions <= 100
        ):
            raise ComponentDataError(
                "repetitionsPerCondition must be between 1 and 100"
            )
        campaign_snapshot = data.get("campaignSnapshot")
        analysis_snapshot = data.get("analysisSnapshot")
        source_digests = data.get("sourceDigests")
        if not isinstance(campaign_snapshot, Mapping):
            raise ComponentDataError("Experiment manifest campaignSnapshot is required")
        if not isinstance(analysis_snapshot, Mapping):
            raise ComponentDataError("Experiment manifest analysisSnapshot is required")
        if not isinstance(source_digests, Mapping) or set(source_digests) != {
            "campaign",
            "analysis",
        }:
            raise ComponentDataError(
                "Experiment manifest requires campaign and analysis source digests"
            )
        return cls(
            manifest_id=_text(data, "manifestId"),
            schema_version=1,
            campaign_id=_text(data, "campaignId"),
            workflow_id=_text(data, "workflowId"),
            analysis_session_id=_text(data, "analysisSessionId"),
            algorithm_version=_text(data, "algorithmVersion"),
            random_seed=random_seed,
            degradation_rates=rates,
            repetitions_per_condition=repetitions,
            campaign_snapshot=dict(campaign_snapshot),
            analysis_snapshot=dict(analysis_snapshot),
            source_digests={str(key): str(value) for key, value in source_digests.items()},
            content_digest=_text(data, "contentDigest"),
            created_at=_timestamp(data.get("createdAt")),
            created_by=_text(data, "createdBy"),
        )

    @property
    def condition_count(self) -> int:
        return len(self.degradation_rates) * self.repetitions_per_condition

    def reproducible_content(self) -> dict[str, Any]:
        """Return the fields protected by ``contentDigest``."""
        return {
            "schemaVersion": self.schema_version,
            "campaignId": self.campaign_id,
            "workflowId": self.workflow_id,
            "analysisSessionId": self.analysis_session_id,
            "algorithmVersion": self.algorithm_version,
            "randomSeed": self.random_seed,
            "degradationPlan": {
                "strategy": "deterministic_span_omission",
                "rates": list(self.degradation_rates),
                "repetitionsPerCondition": self.repetitions_per_condition,
            },
            "campaignSnapshot": self.campaign_snapshot,
            "analysisSnapshot": self.analysis_snapshot,
            "sourceDigests": self.source_digests,
        }

    def to_mapping(self) -> dict[str, Any]:
        return {
            "manifestId": self.manifest_id,
            **self.reproducible_content(),
            "conditionCount": self.condition_count,
            "contentDigest": self.content_digest,
            "createdAt": _iso(self.created_at),
            "createdBy": self.created_by,
        }
