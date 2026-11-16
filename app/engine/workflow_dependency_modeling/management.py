"""Hybrid workflow editing over live logical services and bounded trace windows.

All revisions are append-only. User classifications remain declarations; observed
calls never become experimentally validated dependency labels through this editor.
"""
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
from uuid import uuid4

from sqlalchemy import select, inspect
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_engine
from app.engine.deployment_topology_modeling.service import discover_live
from app.engine.workflow_dependency_modeling.analysis_session_service import approved_jaeger_url
from app.engine.workflow_dependency_modeling.auto_discovery import discover_root_workflows, select_workflow_traces
from app.engine.workflow_dependency_modeling.interaction_extractor import extract_interactions
from app.engine.workflow_dependency_modeling.observed_discovery import read_trace_window
from app.engine.workflow_dependency_modeling.trace_reader import parse_spans
from app.engine.workflow_dependency_modeling.trace_source import JaegerTraceSource
from app.engine.workflow_dependency_modeling.trace_validator import validate_trace_quality
from app.shared.evidence_errors import EvidenceError
from app.shared.persistence.models import WorkflowManagementRevision
from app.shared.schemas.workflow import WorkflowGraph, WorkflowEdge
from app.shared.schemas.workflow_management import WorkflowEdit, RecordingStart

LIMITS = (
    "Calls and trace presence are observed evidence, not proof of mandatory dependencies. "
    "User labels are declared. Reviewed graphs are conditional research inputs, not experimentally validated graphs."
    " Supporting and Optional dependencies project to optionalServices in the shared graph. Only Mandatory paths from the entry imply workflow-wide mandatory participation; conditional dependencies are not represented."
)


def refuse(code, message, status=409):
    return EvidenceError(code, message, http_status=status, component="workflow_dependency_modeling")


@lru_cache(maxsize=4)
def _engine(url):
    return get_engine(url)


class ManagementRepository:
    def __init__(self, engine):
        self.engine = engine

    def versions(self, kind, identity=None):
        statement = select(WorkflowManagementRevision).where(WorkflowManagementRevision.kind == kind)
        if identity:
            statement = statement.where(WorkflowManagementRevision.identity == identity)
        try:
            with Session(self.engine) as session:
                return [deepcopy(row.payload) for row in session.scalars(statement.order_by(WorkflowManagementRevision.version))]
        except SQLAlchemyError:
            raise refuse("WORKFLOW_STORE_UNAVAILABLE", "Workflow storage cannot be read. Check the database and run the pending migrations.", 503) from None

    def latest(self, kind, identity):
        records = self.versions(kind, identity)
        if not records:
            raise refuse("WORKFLOW_DOCUMENT_NOT_FOUND", "The saved workflow or recording was not found.", 404)
        return records[-1]

    def list_latest(self, kind):
        latest = {}
        for record in self.versions(kind):
            latest[record["id"]] = record
        return sorted(latest.values(), key=lambda row: row["updatedAt"], reverse=True)

    def append(self, kind, identity, expected, payload):
        try:
            with Session(self.engine) as session, session.begin():
                current = session.scalar(select(WorkflowManagementRevision.version).where(
                    WorkflowManagementRevision.kind == kind, WorkflowManagementRevision.identity == identity
                ).order_by(WorkflowManagementRevision.version.desc()).limit(1)) or 0
                if current != expected:
                    raise refuse("WORKFLOW_VERSION_CONFLICT", "This record changed elsewhere. Reload it before saving.")
                record = {**deepcopy(payload), "id": identity, "version": current + 1,
                          "updatedAt": datetime.now(timezone.utc).isoformat()}
                session.add(WorkflowManagementRevision(kind=kind, identity=identity, version=current + 1, payload=record))
            return record
        except IntegrityError:
            raise refuse("WORKFLOW_VERSION_CONFLICT", "Another save won the version check. Reload before saving.") from None
        except SQLAlchemyError:
            raise refuse("WORKFLOW_STORE_UNAVAILABLE", "The workflow revision could not be stored. Check the database and reload before retrying.", 503) from None


def repository():
    return ManagementRepository(_engine(get_settings().database_url))


def catalog(namespace):
    discovered = discover_live(namespace=namespace)
    return {"namespace": namespace, "connectionFingerprint": discovered.cluster.connection_fingerprint,
            "capturedAt": discovered.captured_at, "status": discovered.status,
            "services": [service.model_dump(by_alias=True) for service in discovered.graph.services],
            "nodes": [node.model_dump(by_alias=True) for node in discovered.graph.nodes],
            "unresolved": [item.model_dump(by_alias=True) for item in discovered.unresolved]}


def _bound(namespace, fingerprint):
    facts = catalog(namespace)
    if facts["connectionFingerprint"] != fingerprint:
        raise refuse("WORKFLOW_CLUSTER_CHANGED", "The connection changed. Reload live services before continuing.")
    return facts


def _graph(edit, identity):
    """Project declared edge labels to the existing service-level prediction contract conservatively."""
    if not edit.nodes or not edit.entry_service or edit.criticality is None:
        raise ValueError("Review requires services, an entry service and an explicit criticality")
    if any(edge.dependency == "Unknown" for edge in edit.edges):
        raise ValueError("Resolve every Unknown dependency before reviewing for prediction")
    mandatory = {edit.entry_service}
    # Only mandatory paths from the entry imply workflow-wide mandatory participation.
    while True:
        expanded = mandatory | {edge.target for edge in edit.edges if edge.source in mandatory and edge.dependency == "Mandatory"}
        if expanded == mandatory:
            break
        mandatory = expanded
    reachable = {edit.entry_service}
    while True:
        expanded = reachable | {edge.target for edge in edit.edges if edge.source in reachable}
        if expanded == reachable:
            break
        reachable = expanded
    if reachable != {node.service for node in edit.nodes}:
        raise ValueError("Every service must be reachable from the entry before review")
    return WorkflowGraph(workflow_id=identity, criticality=edit.criticality,
        mandatory_services=sorted(mandatory), optional_services=sorted(reachable - mandatory),
        edges=[WorkflowEdge(source=edge.source, target=edge.target) for edge in edit.edges])


def candidate_identity(candidate):
    """A stable selector for legacy recording candidates without a saved candidateId."""
    return candidate.get("candidateId") or hashlib.sha256(json.dumps({
        "name": candidate.get("name"), "entry": candidate.get("entryService"),
        "traces": sorted(candidate.get("traceIds", []))}, sort_keys=True).encode()).hexdigest()[:24]


def evidence_key(ref):
    return f"{ref.kind}:{ref.id}:{ref.version}:{ref.candidate_id}"


def selected_evidence(edit, repo):
    """Pin evidence to a source revision and candidate, rather than pooling other tests/operations."""
    observations, suggestions, sources = {}, {}, []
    scoped = set()
    for ref in edit.evidence_refs:
        records = repo.versions(ref.kind, ref.id)
        record = next((r for r in records if r["version"] == ref.version), None)
        if record is None:
            raise ValueError("The selected evidence revision is unavailable")
        if record["namespace"] != edit.namespace or record["connectionFingerprint"] != edit.connection_fingerprint:
            raise refuse("WORKFLOW_RECORDING_MISMATCH", "Evidence belongs to another cluster or namespace")
        if record["state"] != "stopped":
            raise ValueError("Stop recording before attaching evidence")
        candidate = next((c for c in record["candidates"] if candidate_identity(c) == ref.candidate_id), None)
        if candidate is None:
            raise ValueError("The selected candidate is unavailable in this evidence revision")
        key = evidence_key(ref)
        sources.append({**ref.model_dump(by_alias=True), "key": key, "name": candidate["name"],
                        "execution": candidate.get("execution"), "outcome": candidate.get("outcome"),
                        "unmappedServices": candidate.get("unmappedServices", []), "coverage": candidate.get("coverage"),
                        "correlation": record.get("correlation"), "limits": record.get("limits")})
        for edge in candidate["edges"]:
            pair = edge["source"], edge["target"]
            observations.setdefault(pair, []).extend({**e, "evidenceKey": key, "sourceKind": ref.kind} for e in edge["evidence"])
            suggestions.setdefault(pair, []).extend({**d, "evidenceKey": key, "testName": candidate["name"], "basis": "declared"} for d in edge.get("declarations", []))
        if ref.kind == "recording":
            scoped.add(ref.id)
    # Backward compatibility: legacy documents attached entire recording sessions.
    for identity in dict.fromkeys(edit.recording_ids):
        if identity in scoped:
            continue
        record = repo.latest("recording", identity)
        if record["namespace"] != edit.namespace or record["connectionFingerprint"] != edit.connection_fingerprint:
            raise refuse("WORKFLOW_RECORDING_MISMATCH", "Recording belongs to another cluster or namespace")
        if record["state"] != "stopped":
            raise ValueError("Stop recording before attaching evidence")
        sources.append({"kind": "recording", "id": identity, "version": record["version"], "scope": "legacy whole recording", "correlation": record.get("correlation")})
        for candidate in record.get("candidates", []):
            for edge in candidate["edges"]:
                observations.setdefault((edge["source"], edge["target"]), []).extend(edge["evidence"])
    return observations, suggestions, sources


def save_workflow(edit: WorkflowEdit, identity=None, repo=None):
    repo = repo or repository()
    identity = identity or uuid4().hex
    facts = _bound(edit.namespace, edit.connection_fingerprint)
    prior = None
    if edit.expected_version:
        prior = repo.latest("workflow", identity)
        if prior["namespace"] != edit.namespace or prior["connectionFingerprint"] != edit.connection_fingerprint:
            raise refuse("WORKFLOW_CLUSTER_CHANGED", "A revision must keep its original cluster and namespace. Create a new workflow here.")
        if prior.get("deleted"):
            raise refuse("WORKFLOW_DELETED", "This workflow was deleted. Duplicate a historical version to restore it.")
    present = {service["name"] for service in facts["services"]}
    missing = {node.service for node in edit.nodes} - present
    if missing:
        raise refuse("WORKFLOW_SERVICES_UNAVAILABLE", "Services no longer found in the selected namespace: " + ", ".join(sorted(missing)))
    observations, suggestions, sources = selected_evidence(edit, repo)
    # Older editor versions attached whole recordings. Keep their already-saved
    # references when a new scoped selection from that session is merged later.
    retained_legacy = set()
    for old_edge in (prior or {}).get("edges", []):
        for item in old_edge.get("evidence", []):
            if item.get("recordingId") in edit.recording_ids and not item.get("evidenceKey"):
                pair = old_edge["source"], old_edge["target"]
                existing = observations.setdefault(pair, [])
                if item not in existing:
                    existing.append(deepcopy(item))
                    retained_legacy.add(item["recordingId"])
    for recording_id in retained_legacy:
        sources.append({"kind": "recording", "id": recording_id,
                        "scope": "legacy references retained from prior workflow revision"})
    edges, classification_conflicts = [], []
    protected = {(e["source"], e["target"]): e for e in (prior or {}).get("edges", [])
                 if e.get("classificationBasis") in {"validated", "experimentally_validated"}}
    incoming_pairs = {(e.source, e.target) for e in edit.edges}
    if set(protected) - incoming_pairs:
        raise ValueError("A validated relationship cannot be removed here; use its controlled assessment workflow")
    for edge in edit.edges:
        pair = edge.source, edge.target
        proven = protected.get(pair)
        if proven and edge.dependency != proven["dependency"]:
            raise ValueError("A validated classification cannot be overwritten by an editor import or declaration")
        evidence = deepcopy(observations.get(pair, []))
        if proven:
            for item in proven.get("evidence", []):
                if item not in evidence:
                    evidence.append(deepcopy(item))
        declarations = suggestions.get(pair, [])
        conflicts = [d for d in declarations if d["dependency"] not in {"Unknown", edge.dependency}
                     and d["evidenceKey"] not in edge.reviewed_evidence]
        classification_conflicts.extend({"source": edge.source, "target": edge.target, **d,
            "reason": f"Imported {d['dependency']} declaration differs from {edge.dependency}; review this connection."} for d in conflicts)
        edges.append({**(proven or {}), **edge.model_dump(by_alias=True),
                      "classificationBasis": proven["classificationBasis"] if proven else "declared",
                      "interactionBasis": "observed" if evidence else "declared", "evidence": evidence,
                      "declarations": declarations, "classificationConflicts": conflicts})
    if edit.reviewed and classification_conflicts:
        raise ValueError("Review conflicting dependency declarations before saving a reviewed workflow")
    chosen = {node.service for node in edit.nodes}
    conflicts = [{"source": source, "target": target, "reason": "An observed connection was omitted from this declaration; omission does not prove absence."}
                 for source, target in observations if source in chosen and target in chosen
                 and (source, target) not in {(edge.source, edge.target) for edge in edit.edges}]
    graph = _graph(edit, identity).model_dump(by_alias=True) if edit.reviewed else None
    document = edit.model_dump(by_alias=True, exclude={"expected_version"})
    document.update({"edges": edges, "conflicts": conflicts, "classificationConflicts": classification_conflicts, "evidenceSources": sources, "graph": graph,
                     "evidenceBasis": "declared", "experimentallyValidated": False,
                     "limits": LIMITS, "deleted": False})
    return repo.append("workflow", identity, edit.expected_version, document)


def delete_workflow(identity, expected, actor, repo=None):
    repo = repo or repository()
    current = repo.latest("workflow", identity)
    if not actor.strip():
        raise ValueError("Say who is deleting this workflow")
    return repo.append("workflow", identity, expected, {**current, "actor": actor.strip(), "deleted": True,
                                                        "reviewed": False, "graph": None})


def _validated_service_map(service_map, facts):
    present = {service["name"] for service in facts["services"]}
    if any(not source.strip() or target not in present for source, target in service_map.items()):
        raise ValueError("Explicit trace-service mappings must target discovered Kubernetes services")
    return dict(service_map)


def start_recording(request: RecordingStart, repo=None):
    facts = _bound(request.namespace, request.connection_fingerprint)
    service_map = _validated_service_map(request.service_map, facts)
    endpoint = approved_jaeger_url(request.jaeger_url)
    source = JaegerTraceSource.from_url(endpoint)
    try:
        source.list_services()  # refuse explicitly if tracing is unavailable; manual editing remains independent
    except ConnectionError:
        raise refuse("WORKFLOW_TRACE_UNAVAILABLE", "The trace service cannot be read. Manual workflows still work. Check that your application already sends OpenTelemetry traces to Jaeger; recording does not instrument or change workloads.", 503) from None
    return (repo or repository()).append("recording", uuid4().hex, 0, {
        "namespace": request.namespace, "connectionFingerprint": request.connection_fingerprint,
        "jaegerUrl": endpoint, "startedMicros": int(datetime.now(timezone.utc).timestamp() * 1_000_000),
        "endedMicros": None, "state": "recording", "candidates": [], "serviceMap": service_map,
        "servicesAtStart": [service["name"] for service in facts["services"]],
        "correlation": "Fixed time window, trace identity, root service and operation. Exact names or explicit mappings identify Kubernetes services. Concurrent operations stay separate candidates; unrelated requests using the same operation cannot be distinguished automatically.",
        "limits": LIMITS})


def stop_recording(identity, repo=None, service_map=None):
    repo = repo or repository()
    record = repo.latest("recording", identity)
    facts = _bound(record["namespace"], record["connectionFingerprint"])
    if service_map is not None:
        service_map = _validated_service_map(service_map, facts)
    changed_mapping = service_map is not None and service_map != record.get("serviceMap", {})
    if record["state"] == "recording" or changed_mapping:
        record = repo.append("recording", identity, record["version"], {
            **record, "state": "stopped", "serviceMap": service_map if service_map is not None else record.get("serviceMap", {}),
            "endedMicros": record["endedMicros"] or int(datetime.now(timezone.utc).timestamp() * 1_000_000)})
    # Re-collect on an explicit Stop/refresh only, preserving every earlier evidence revision.
    source = JaegerTraceSource.from_url(approved_jaeger_url(record["jaegerUrl"]))
    traces, gaps = {}, []
    try:
        recorded = source.list_services()
    except ConnectionError:
        recorded = []
        gaps.append({"reason": "trace_service_unavailable"})
    excluded = {"jaeger-all-in-one", "jaeger-query", "jaeger"}
    available = sorted({name for name in recorded if name and name.lower() not in excluded})
    known = set(record["servicesAtStart"])
    mappings = record.get("serviceMap", {})
    # Exact and explicitly mapped names come first, but unfamiliar names are queried too.
    relevant = sorted(available, key=lambda name: (name not in known and name not in mappings, name))
    queried = relevant[:12]
    duration = max(1, math.ceil((record["endedMicros"] - record["startedMicros"]) / 1_000_000))
    for service in queried:
        try:
            batch = read_trace_window(source, service, f"{duration}s", page_size=100,
                                      max_queries=8, now_us=record["endedMicros"])
            for trace in batch:
                spans = parse_spans([trace])
                starts = [span.start_time_us for span in spans if span.start_time_us is not None]
                if starts and record["startedMicros"] <= min(starts) <= record["endedMicros"]:
                    traces[trace["traceID"]] = trace
        except (ConnectionError, ValueError, TypeError, KeyError):
            gaps.append({"service": service, "reason": "query_incomplete"})
    gaps.extend({"service": service, "reason": "service_query_limit"} for service in relevant[12:])
    candidates = []
    all_unmapped = set()

    def logical(name):
        mapped = mappings.get(name, name)
        return mapped if mapped in known else None

    for summary in discover_root_workflows(list(traces.values()), route_patterns=()):
        selected = select_workflow_traces(list(traces.values()), summary.operation_name, summary.root_service, route_patterns=())
        interactions = extract_interactions(parse_spans(selected))
        observed = {mapped for name in summary.participating_services if (mapped := logical(name))}
        unmapped = {name for name in summary.participating_services if logical(name) is None}
        all_unmapped.update(unmapped)
        edges = {}
        for interaction in interactions:
            for edge in interaction.edges:
                source_name, target_name = logical(edge.source), logical(edge.target)
                if not source_name or not target_name or source_name == target_name:
                    continue
                evidence = edges.setdefault((source_name, target_name), [])
                evidence.extend({"recordingId": identity, "traceId": interaction.trace_id,
                                 "traceSourceService": edge.source, "traceTargetService": edge.target,
                                 "identityBasis": "explicit_mapping" if edge.source in mappings or edge.target in mappings else "exact_name",
                                 **item} for item in interaction.edge_evidence.get((edge.source, edge.target), []))
        quality = validate_trace_quality(selected)
        candidates.append({"name": summary.operation_name, "entryService": logical(summary.root_service),
            "traceEntryService": summary.root_service, "services": sorted(observed), "unmappedServices": sorted(unmapped),
            "traceIds": sorted({trace["traceID"] for trace in selected if trace.get("traceID")}),
            "traceQuality": {"valid": quality.valid_count, "partial": quality.partial_count, "invalid": quality.invalid_count},
            "coverage": {"traces": len(selected), "mappedServices": len(observed), "unmappedServices": len(unmapped)},
            "edges": [{"source": key[0], "target": key[1], "dependency": "Unknown", "evidence": evidence}
                      for key, evidence in sorted(edges.items())]})
    for candidate in candidates:
        candidate["candidateId"] = candidate_identity(candidate)
    gaps.extend({"service": service, "reason": "service_mapping_required"} for service in sorted(all_unmapped))
    if any(reason.get("reason") == "trace_service_unavailable" for reason in gaps):
        status = "trace_service_unavailable"
    elif not traces:
        status = "no_traces"
    elif not candidates:
        status = "no_candidates"
    elif not any(candidate["services"] for candidate in candidates):
        status = "no_matching_services"
    elif gaps or len(queried) < len(relevant):
        status = "partial"
    else:
        status = "discovered"
    return repo.append("recording", identity, record["version"], {**record, "candidates": candidates,
        "coverage": {"gaps": gaps, "servicesRecorded": available, "servicesQueried": queried,
                     "servicesSkipped": relevant[12:], "unmappedServices": sorted(all_unmapped), "tracesFetched": len(traces),
                     "status": status,
                     "limits": "12 trace services (exact and explicitly mapped names first), 8 queries per service, 100 traces per query. Sampling, ingestion delay, concurrent traffic and name/namespace ambiguity may hide or misattribute traces."}})


def reviewed_workflows(repo=None):
    """Reviewed declarations for downstream research, bound to the current connection."""
    repo = repo or repository()
    if not inspect(repo.engine).has_table("workflow_management_revisions"):
        return []
    records = [row for row in repo.list_latest("workflow")
               if row.get("reviewed") and not row.get("deleted") and row.get("graph")]
    bindings = {}
    for row in records:
        if row["namespace"] not in bindings:
            bindings[row["namespace"]] = catalog(row["namespace"])["connectionFingerprint"]
    return [row for row in records if row["connectionFingerprint"] == bindings[row["namespace"]]]


def document_digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def list_documents(kind="workflow"):
    rows = [row for row in repository().list_latest(kind) if not row.get("deleted")]
    for row in rows:
        for candidate in row.get("candidates", []):
            candidate.setdefault("candidateId", candidate_identity(candidate))
    return rows


def read_document(identity, version=None, kind="workflow"):
    if version is None:
        return repository().latest(kind, identity)
    for row in repository().versions(kind, identity):
        if row["version"] == version:
            return row
    raise refuse("WORKFLOW_DOCUMENT_NOT_FOUND", "That version was not found.", 404)


def document_history(identity):
    return repository().versions("workflow", identity)
