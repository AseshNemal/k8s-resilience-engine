"""Load separated synthetic scenario, evidence, and reference fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.engine.workflow_dependency_modeling.models import (
    ComponentDataError,
    ReferenceWorkflow,
    WorkflowEvidenceSource,
    WorkflowScenario,
)
from app.engine.workflow_dependency_modeling.reference_source import ReferenceSource
from app.engine.workflow_dependency_modeling.trace_reader import load_traces_from_file
from app.shared.sample_loader import SAMPLE_DATA_DIR

SCENARIOS_DIR = SAMPLE_DATA_DIR / "workflow_scenarios"
EVIDENCE_DIR = SAMPLE_DATA_DIR / "workflow_evidence"
REFERENCES_DIR = SAMPLE_DATA_DIR / "workflow_references"
_REPOSITORY_ROOT = SAMPLE_DATA_DIR.parent.resolve()

def _load_json(directory: Path, workflow_id: str, kind: str) -> dict[str, Any]:
    if not workflow_id or Path(workflow_id).name != workflow_id:
        raise ComponentDataError(f"Invalid {kind} workflow ID: {workflow_id!r}")
    path = directory / f"{workflow_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"{kind} '{workflow_id}' not found in {directory}")
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ComponentDataError(f"{kind} '{workflow_id}' must contain a JSON object")
    return data


def get_scenario(workflow_id: str) -> WorkflowScenario:
    scenario = WorkflowScenario.from_mapping(
        _load_json(SCENARIOS_DIR, workflow_id, "Workflow scenario")
    )
    if scenario.workflow_id != workflow_id:
        raise ComponentDataError(
            f"Scenario ID '{scenario.workflow_id}' does not match requested '{workflow_id}'"
        )
    return scenario


def get_evidence_source(workflow_id: str) -> WorkflowEvidenceSource:
    evidence = WorkflowEvidenceSource.from_mapping(
        _load_json(EVIDENCE_DIR, workflow_id, "Workflow evidence")
    )
    if evidence.workflow_id != workflow_id:
        raise ComponentDataError(
            f"Evidence ID '{evidence.workflow_id}' does not match requested '{workflow_id}'"
        )
    return evidence


def get_reference(workflow_id: str) -> ReferenceWorkflow:
    reference = ReferenceWorkflow.from_mapping(
        _load_json(REFERENCES_DIR, workflow_id, "Workflow reference")
    )
    if reference.workflow_id != workflow_id:
        raise ComponentDataError(
            f"Reference ID '{reference.workflow_id}' does not match requested '{workflow_id}'"
        )
    return reference


def load_fixture_traces(workflow_id: str) -> list[dict]:
    evidence = get_evidence_source(workflow_id)
    trace_path = (_REPOSITORY_ROOT / evidence.trace_file).resolve()
    traces_root = (SAMPLE_DATA_DIR / "traces").resolve()
    if traces_root not in trace_path.parents:
        raise ComponentDataError(
            f"Evidence trace file for '{workflow_id}' must remain under {traces_root}"
        )
    if not trace_path.is_file():
        raise FileNotFoundError(
            f"Trace evidence for '{workflow_id}' not found at {trace_path}"
        )
    return load_traces_from_file(trace_path)


def list_fixture_workflows() -> list[dict[str, Any]]:
    workflows: list[dict[str, Any]] = []
    if not SCENARIOS_DIR.exists():
        return workflows
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        scenario = get_scenario(path.stem)
        evidence = get_evidence_source(path.stem)
        workflows.append(
            {
                "workflowId": scenario.workflow_id,
                "description": scenario.description,
                "criticality": scenario.criticality,
                "entryService": scenario.entry_service,
                "sourceKind": evidence.source_kind,
                "synthetic": evidence.synthetic,
                "referenceAvailable": (REFERENCES_DIR / path.name).is_file(),
            }
        )
    return workflows


# The synthetic engineering references are for research runs and tests only. Nothing in the application
# configures this source; a caller has to pass it (or configure it) deliberately.
FIXTURE_REFERENCE_SOURCE = ReferenceSource(
    name="sample_data/workflow_references (synthetic engineering fixtures)",
    load=get_reference,
)
