"""A workflow the developer DECLARES, for when Component 1 has none to give: Member 4's own, independent source.

Member 4 needs a ``WorkflowGraph`` to size a failure. Component 1 produces one from real traces and freezes it after an
assessment is approved. When there are no traces (an application with no tracing, a cluster where it cannot be set up) there
is no frozen workflow, and without one the recommendation page can show nothing. This module lets a person state the workflow
themselves, in a file, so Member 4 can still work, WITHOUT Component 1 and without changing it.

What a declaration is, and is not:

* it is the shared ``WorkflowGraph`` contract (unchanged), read from a JSON file the backend's environment names
  (``RESILIENCE_DECLARED_WORKFLOW_PATH``, set where the backend runs, never taken from a request, the same way the
  manifest path is). It adds who declared it and a digest of the file, so what was used can be identified later;
* it is NOT observed. Nothing checked that the dependencies are real, that mandatory services are mandatory, or that the edges
  exist. A mistake in the file is a mistake in every number derived from it. Everything computed from a declaration is labelled
  ``declared-workflow``, and its limits travel with it;
* it is NOT approved or frozen. The approval gate trusts only evidence from the live route over a frozen workflow, so a
  recommendation computed from a declaration can be read and studied but never approved, applied or validated. That is
  deliberate: declared is not the same as discovered, and the gate does not pretend it is.

File format (one object, or ``{"workflows": [ ... ]}``)::

    {"workflowId": "productpage-view", "criticality": 0.8, "declaredBy": "a name",
     "mandatoryServices": ["productpage", "details", "reviews"], "optionalServices": ["ratings"],
     "edges": [{"source": "productpage", "target": "details"}, ...], "note": "optional"}
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph

ENV_NAME = "RESILIENCE_DECLARED_WORKFLOW_PATH"
MAX_BYTES = 256 * 1024
COMPONENT = "recommendation"

NOT_OBSERVED = (
    "Declared by a person, not observed from traces and not approved or frozen by Component 1. The dependencies, which "
    "services are mandatory and the edges are as stated and unchecked. A recommendation computed from it cannot be "
    "approved, applied or validated."
)


class DeclaredWorkflowError(EvidenceError):
    def __init__(self, code: str, message: str, *, http_status: int = 409) -> None:
        super().__init__(code, message, http_status=http_status, component=COMPONENT)


@dataclass(frozen=True)
class DeclaredWorkflow:
    graph: WorkflowGraph
    declared_by: str
    note: str | None
    declaration_sha256: str      # of the whole file, so the exact declaration is identifiable
    source_name: str             # the file's name only, never its path

    def to_dict(self) -> dict[str, Any]:
        return {"workflowId": self.graph.workflow_id, "declaredBy": self.declared_by, "note": self.note,
                "declarationSha256": self.declaration_sha256, "sourceName": self.source_name, "observed": False,
                "approved": False, "limits": NOT_OBSERVED}


def declared_workflow_path() -> Path | None:
    """The declaration file the backend's environment names, or None. A request can never supply it."""
    raw = (os.environ.get(ENV_NAME) or "").strip()
    return Path(raw).expanduser() if raw else None


def _invalid(message: str) -> DeclaredWorkflowError:
    return DeclaredWorkflowError("DECLARED_WORKFLOW_INVALID", message, http_status=422)


def _services(entry: dict[str, Any], key: str, label: str) -> list[str]:
    values = entry.get(key, [])
    if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise _invalid(f"{label}: {key} must be a list of service names")
    names = [v.strip() for v in values]
    if len(set(names)) != len(names):
        raise _invalid(f"{label}: {key} names a service twice")
    return names


def _build(entry: Any, digest: str, source_name: str) -> DeclaredWorkflow:
    if not isinstance(entry, dict):
        raise _invalid("each workflow must be an object")
    workflow_id = entry.get("workflowId")
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise _invalid("a workflow needs a workflowId")
    label = f"workflow {workflow_id!r}"
    declared_by = entry.get("declaredBy")
    if not isinstance(declared_by, str) or not declared_by.strip():
        raise _invalid(f"{label}: say who declared it (declaredBy)")
    criticality = entry.get("criticality")
    if isinstance(criticality, bool) or not isinstance(criticality, (int, float)) or not math.isfinite(criticality) \
            or not 0.0 <= criticality <= 1.0:
        raise _invalid(f"{label}: criticality must be a number between 0 and 1, stated by you (there is no default)")
    mandatory, optional = _services(entry, "mandatoryServices", label), _services(entry, "optionalServices", label)
    if not mandatory:
        raise _invalid(f"{label}: a workflow needs at least one mandatory service")
    if set(mandatory) & set(optional):
        raise _invalid(f"{label}: a service cannot be both mandatory and optional: {sorted(set(mandatory) & set(optional))}")
    declared = set(mandatory) | set(optional)
    raw_edges = entry.get("edges", [])
    if not isinstance(raw_edges, list):
        raise _invalid(f"{label}: edges must be a list")
    edges = []
    for edge in raw_edges:
        if not isinstance(edge, dict) or not isinstance(edge.get("source"), str) or not isinstance(edge.get("target"), str):
            raise _invalid(f"{label}: each edge needs a source and a target")
        if edge["source"] not in declared or edge["target"] not in declared:
            raise _invalid(f"{label}: edge {edge['source']} -> {edge['target']} names a service the workflow does not declare")
        edges.append(WorkflowEdge(source=edge["source"], target=edge["target"]))
    try:
        graph = WorkflowGraph(workflow_id=workflow_id.strip(), criticality=float(criticality), mandatory_services=mandatory,
                              optional_services=optional, edges=edges)
    except ValidationError as error:      # the contract itself refused it
        raise _invalid(f"{label}: {error.errors()[0]['msg']}") from error
    note = entry.get("note")
    return DeclaredWorkflow(graph, declared_by.strip(), note if isinstance(note, str) and note.strip() else None, digest, source_name)


def load_declared_workflows(path: str | Path | None = None) -> list[DeclaredWorkflow]:
    """Every workflow declared in the file. No file named is an empty list; a file that cannot be used is an error."""
    target = Path(path).expanduser() if path else declared_workflow_path()
    if target is None:
        return []
    try:
        data = target.read_bytes()
    except OSError:
        raise DeclaredWorkflowError("DECLARED_WORKFLOW_UNREADABLE", "The declared-workflow file cannot be read.") from None
    if len(data) > MAX_BYTES:
        raise _invalid(f"the declaration file is larger than {MAX_BYTES // 1024} KiB")
    try:
        document = json.loads(data)
    except ValueError:
        raise _invalid("the declaration file is not valid JSON") from None
    entries = document.get("workflows") if isinstance(document, dict) and "workflows" in document else [document]
    if not isinstance(entries, list) or not entries:
        raise _invalid("the declaration file holds no workflow")
    digest = hashlib.sha256(data).hexdigest()
    workflows = [_build(entry, digest, target.name) for entry in entries]
    ids = [w.graph.workflow_id for w in workflows]
    if len(set(ids)) != len(ids):
        raise _invalid("two workflows share an id")
    return workflows


def get_declared_workflow(workflow_id: str, path: str | Path | None = None) -> DeclaredWorkflow:
    wanted = (workflow_id or "").strip()
    for workflow in load_declared_workflows(path):
        if workflow.graph.workflow_id == wanted:
            return workflow
    raise DeclaredWorkflowError("DECLARED_WORKFLOW_UNAVAILABLE", f"No workflow {wanted!r} is declared.", http_status=404)
