"""Editable workflow contracts. Dependency labels are declarations, never experimental proof."""
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.shared.schemas.base import APIModel


class EditorNode(APIModel):
    model_config = ConfigDict(extra="forbid")
    service: str = Field(min_length=1, max_length=253)
    x: float = Field(ge=0, le=4000, allow_inf_nan=False)
    y: float = Field(ge=0, le=4000, allow_inf_nan=False)


class WorkflowEvidenceRef(APIModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, alias_generator=APIModel.model_config["alias_generator"])
    kind: Literal["recording", "test"]
    id: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)
    candidate_id: str = Field(min_length=1, max_length=160)


class EditorEdge(APIModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=253)
    target: str = Field(min_length=1, max_length=253)
    dependency: Literal["Mandatory", "Optional", "Supporting", "Unknown"] = "Unknown"
    reviewed_evidence: list[str] = Field(default_factory=list, max_length=100, alias="reviewedEvidence")


class WorkflowEdit(APIModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, alias_generator=APIModel.model_config["alias_generator"])
    name: str = Field(min_length=1, max_length=160)
    namespace: str = Field(min_length=1, max_length=63)
    connection_fingerprint: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=0)
    actor: str = Field(min_length=1, max_length=100)
    entry_service: str | None = Field(default=None, max_length=253)
    criticality: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    nodes: list[EditorNode] = Field(default_factory=list, max_length=100)
    edges: list[EditorEdge] = Field(default_factory=list, max_length=500)
    recording_ids: list[str] = Field(default_factory=list, max_length=20)
    evidence_refs: list[WorkflowEvidenceRef] = Field(default_factory=list, max_length=100)
    reviewed: bool = False

    @model_validator(mode="after")
    def valid_graph(self):
        refs = [(r.kind, r.id, r.version, r.candidate_id) for r in self.evidence_refs]
        if len(refs) != len(set(refs)):
            raise ValueError("Each evidence selection may be attached only once")
        names = [node.service for node in self.nodes]
        if len(names) != len(set(names)):
            raise ValueError("A logical service may appear only once in a workflow")
        pairs = [(edge.source, edge.target) for edge in self.edges]
        if len(pairs) != len(set(pairs)):
            raise ValueError("A directional connection may appear only once")
        if any(edge.source not in names or edge.target not in names or edge.source == edge.target for edge in self.edges):
            raise ValueError("Connections must join two different workflow services")
        if self.entry_service is not None and self.entry_service not in names:
            raise ValueError("The entry service must be on the canvas")
        if not self.actor.strip() or not self.name.strip():
            raise ValueError("Name and actor must contain text")
        return self


class ServiceMapping(APIModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, alias_generator=APIModel.model_config["alias_generator"])
    service_map: dict[str, str] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def valid_mappings(self):
        for trace_name, service_name in self.service_map.items():
            if not trace_name.strip() or len(trace_name) > 253 or not service_name.strip() or len(service_name) > 253:
                raise ValueError("Trace-service mappings need non-empty names of at most 253 characters")
        return self


class RecordingStart(ServiceMapping):
    namespace: str = Field(min_length=1, max_length=63)
    connection_fingerprint: str = Field(min_length=1, max_length=200)
    jaeger_url: str = Field(min_length=1, max_length=2048)


class RecordingStop(ServiceMapping):
    pass


class WorkflowDecision(APIModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, alias_generator=APIModel.model_config["alias_generator"])
    expected_version: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=100)
