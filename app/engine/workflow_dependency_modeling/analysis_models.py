"""Component-local contracts for offline workflow analysis."""

from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.shared.schemas.base import APIModel
from app.shared.schemas.workflow import WorkflowGraph


class WorkflowAnalysisRequest(APIModel):
    model_config = ConfigDict(extra="forbid")

    workflow_definition: dict[str, Any] = Field(
        description="Business definition; output dependency labels and reference answers are forbidden.",
        examples=[{
            "workflowId": "dispatch", "description": "Dispatch a delivery", "criticality": 0.8,
            "entryService": "gateway", "successCondition": {
                "protocol": "http", "terminalService": "gateway",
                "operationName": "POST /deliveries", "httpStatusCodes": [201],
            },
        }],
    )
    operation_name: str = Field(min_length=1, description="Recorded root operation or normalized HTTP method and route.")
    source_type: Literal["offline_jaeger", "zipkin", "tempo", "vendor_tracing"] = "offline_jaeger"
    traces: list[dict[str, Any] | list[dict[str, Any]]] | dict[str, Any] = Field(
        description="Trace export in the selected format. Jaeger accepts a trace list or a data envelope.",
    )
    mandatory_threshold: float = Field(default=1.0, ge=0, le=1, strict=True)
    min_edge_frequency: float = Field(default=0.0, ge=0, le=1, strict=True)


class ServiceEvidence(APIModel):
    service: str
    trace_count: int
    frequency: float
    classification: Literal["MANDATORY_CANDIDATE", "OPTIONAL_CANDIDATE"]


class WorkflowAnalysisResult(APIModel):
    workflow_id: str
    operation_name: str
    classification_status: Literal["frequency_candidates", "insufficient_success_evidence"]
    candidate_graph: WorkflowGraph | None
    input_trace_count: int
    matched_trace_count: int
    unrelated_trace_count: int
    successful_trace_count: int
    failed_trace_count: int
    indeterminate_trace_count: int
    classification_trace_ids: list[str]
    service_evidence: list[ServiceEvidence]
    edge_evidence: list[dict[str, Any]]
    trace_outcomes: list[dict[str, Any]]
    trace_quality: dict[str, Any]
    limitations: list[str]
