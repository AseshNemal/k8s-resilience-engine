"""Persistence ports for controlled workflow experiment campaigns."""

from __future__ import annotations

from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.engine.workflow_dependency_modeling.experiment_campaign_models import (
    ExperimentCampaign,
)
from app.shared.persistence.models import ResearchArtifact


class ExperimentCampaignNotFoundError(LookupError):
    pass


class ExperimentCampaignConflictError(ValueError):
    pass


class ExperimentCampaignRepository(Protocol):
    def create(self, campaign: ExperimentCampaign) -> ExperimentCampaign: ...
    def get(self, campaign_id: str) -> ExperimentCampaign: ...
    def list(self) -> list[ExperimentCampaign]: ...


class InMemoryExperimentCampaignRepository:
    def __init__(self) -> None:
        self._campaigns: dict[str, ExperimentCampaign] = {}
        self._lock = RLock()

    def create(self, campaign: ExperimentCampaign) -> ExperimentCampaign:
        with self._lock:
            if campaign.campaign_id in self._campaigns:
                raise ExperimentCampaignConflictError(
                    f"Experiment campaign '{campaign.campaign_id}' already exists"
                )
            self._campaigns[campaign.campaign_id] = campaign
        return campaign

    def get(self, campaign_id: str) -> ExperimentCampaign:
        with self._lock:
            try:
                return self._campaigns[campaign_id]
            except KeyError as err:
                raise ExperimentCampaignNotFoundError(
                    f"Experiment campaign '{campaign_id}' was not found"
                ) from err

    def list(self) -> list[ExperimentCampaign]:
        with self._lock:
            return sorted(
                self._campaigns.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )


class SqlAlchemyExperimentCampaignRepository:
    _PREFIX = "workflow_experiment_campaign:"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def _artifact_type(cls, campaign_id: str) -> str:
        return f"{cls._PREFIX}{campaign_id}"

    def create(self, campaign: ExperimentCampaign) -> ExperimentCampaign:
        artifact_type = self._artifact_type(campaign.campaign_id)
        with Session(self._engine) as database, database.begin():
            existing = database.scalar(
                select(ResearchArtifact.id).where(
                    ResearchArtifact.artifact_type == artifact_type
                )
            )
            if existing is not None:
                raise ExperimentCampaignConflictError(
                    f"Experiment campaign '{campaign.campaign_id}' already exists"
                )
            database.add(
                ResearchArtifact(
                    artifact_type=artifact_type,
                    payload=campaign.to_mapping(),
                )
            )
        return campaign

    def get(self, campaign_id: str) -> ExperimentCampaign:
        with Session(self._engine) as database:
            artifact = database.scalar(
                select(ResearchArtifact).where(
                    ResearchArtifact.artifact_type == self._artifact_type(campaign_id)
                )
            )
        if artifact is None:
            raise ExperimentCampaignNotFoundError(
                f"Experiment campaign '{campaign_id}' was not found"
            )
        return ExperimentCampaign.from_mapping(artifact.payload)

    def list(self) -> list[ExperimentCampaign]:
        statement = (
            select(ResearchArtifact)
            .where(ResearchArtifact.artifact_type.like(f"{self._PREFIX}%"))
            .order_by(ResearchArtifact.id.desc())
        )
        with Session(self._engine) as database:
            return [
                ExperimentCampaign.from_mapping(item.payload)
                for item in database.scalars(statement)
            ]
