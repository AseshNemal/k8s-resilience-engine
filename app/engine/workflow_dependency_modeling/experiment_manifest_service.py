"""Create and verify reproducible Component 1 experiment manifests."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    AnalysisSessionRepository,
)
from app.engine.workflow_dependency_modeling.analysis_session_service import (
    get_workflow_analysis,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_repository import (
    ExperimentCampaignRepository,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_service import (
    get_experiment_campaign,
)
from app.engine.workflow_dependency_modeling.experiment_manifest_models import (
    ExperimentManifest,
)
from app.engine.workflow_dependency_modeling.experiment_manifest_repository import (
    ExperimentManifestConflictError,
    ExperimentManifestRepository,
    SqlAlchemyExperimentManifestRepository,
)


ALGORITHM_VERSION = "workflow-dependency-modeling/phase-6-reproducibility-1"
DEFAULT_DEGRADATION_RATES = (0.0, 0.05, 0.1, 0.2, 0.3)
_ALLOWED_FIELDS = {"randomSeed", "degradationRates", "repetitionsPerCondition"}
_default_repository: ExperimentManifestRepository | None = None


def configure_experiment_manifest_repository(
    repository: ExperimentManifestRepository,
) -> None:
    global _default_repository
    _default_repository = repository


def _repository(
    repository: ExperimentManifestRepository | None,
) -> ExperimentManifestRepository:
    if repository is not None:
        return repository
    global _default_repository
    if _default_repository is None:
        from app.core.config import get_settings
        from app.core.database import get_engine

        _default_repository = SqlAlchemyExperimentManifestRepository(
            get_engine(get_settings().database_url)
        )
    return _default_repository


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _timestamp(now: datetime | None) -> datetime:
    result = now or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("Experiment manifest timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _controls(data: Mapping[str, Any]) -> tuple[int, tuple[float, ...], int]:
    unknown = set(data) - _ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"Unknown manifest controls: {', '.join(sorted(unknown))}")
    seed = data.get("randomSeed", 1729)
    rates = data.get("degradationRates", list(DEFAULT_DEGRADATION_RATES))
    repetitions = data.get("repetitionsPerCondition", 5)
    if not isinstance(rates, list):
        raise ValueError("degradationRates must be an array")
    # Reuse the model's strict validation for ranges, order, seed, and repetitions.
    try:
        normalized_rates = tuple(float(value) for value in rates)
    except (TypeError, ValueError) as err:
        raise ValueError("degradationRates must contain only numbers") from err
    return seed, normalized_rates, repetitions


def create_experiment_manifest(
    campaign_id: str,
    controls: Mapping[str, Any],
    *,
    actor: str,
    repository: ExperimentManifestRepository | None = None,
    campaign_repository: ExperimentCampaignRepository | None = None,
    analysis_repository: AnalysisSessionRepository | None = None,
    now: datetime | None = None,
) -> ExperimentManifest:
    """Freeze all evidence-query and experiment controls into one artifact."""
    seed, rates, repetitions = _controls(controls)
    campaign = get_experiment_campaign(campaign_id, campaign_repository)
    analysis = get_workflow_analysis(
        campaign.analysis_session_id,
        analysis_repository,
    )
    campaign_snapshot = campaign.to_mapping()
    analysis_snapshot = analysis.to_mapping()
    source_digests = {
        "campaign": _canonical_digest(campaign_snapshot),
        "analysis": _canonical_digest(analysis_snapshot),
    }
    content = {
        "schemaVersion": 1,
        "campaignId": campaign.campaign_id,
        "workflowId": campaign.workflow_id,
        "analysisSessionId": analysis.session_id,
        "algorithmVersion": ALGORITHM_VERSION,
        "randomSeed": seed,
        "degradationPlan": {
            "strategy": "deterministic_span_omission",
            "rates": list(rates),
            "repetitionsPerCondition": repetitions,
        },
        "campaignSnapshot": campaign_snapshot,
        "analysisSnapshot": analysis_snapshot,
        "sourceDigests": source_digests,
    }
    digest = _canonical_digest(content)
    manifest = ExperimentManifest.from_mapping(
        {
            "manifestId": f"manifest:{digest.removeprefix('sha256:')[:24]}",
            **content,
            "contentDigest": digest,
            "createdAt": _timestamp(now).isoformat(),
            "createdBy": actor,
        }
    )
    target = _repository(repository)
    try:
        return target.create(manifest)
    except ExperimentManifestConflictError:
        existing = target.get(manifest.manifest_id)
        if existing.content_digest == manifest.content_digest:
            return existing
        raise


def verify_experiment_manifest(manifest: ExperimentManifest) -> bool:
    """Detect any mutation of the frozen research inputs or controls."""
    return (
        _canonical_digest(manifest.campaign_snapshot)
        == manifest.source_digests.get("campaign")
        and _canonical_digest(manifest.analysis_snapshot)
        == manifest.source_digests.get("analysis")
        and _canonical_digest(manifest.reproducible_content())
        == manifest.content_digest
    )


def get_experiment_manifest(
    manifest_id: str,
    repository: ExperimentManifestRepository | None = None,
) -> ExperimentManifest:
    return _repository(repository).get(manifest_id)


def list_experiment_manifests(
    repository: ExperimentManifestRepository | None = None,
) -> list[ExperimentManifest]:
    return _repository(repository).list()
