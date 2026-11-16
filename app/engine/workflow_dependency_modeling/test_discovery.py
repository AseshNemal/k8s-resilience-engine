"""Test executions → exactly addressed traces → existing call extractor → editor candidates.

No shell, test source scanning, workload changes, or dependency-necessity inference.
Imported metadata is runner-reported; trace correlation is not cluster attestation.
"""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import math
from uuid import uuid4

from . import management
from .analysis_session_service import approved_jaeger_url
from .interaction_extractor import extract_interactions
from .models import SuccessCondition
from .outcome_evaluator import evaluate_trace_success
from .trace_reader import parse_spans
from .trace_source import JaegerTraceSource
from .trace_validator import validate_trace_quality
from .workflow_probe_models import WorkflowProbeDefinition
from .workflow_probe_runner import run_http_workflow_probe
from app.shared.schemas.workflow_test_evidence import TestEvidenceImport, TestExecutionEvidence, HttpTestRun


def _trace_matches(trace, execution):
    """Exact IDs plus a bounded execution window; no time-window search fallback."""
    starts = [s.get("startTime") for s in trace.get("spans", []) if isinstance(s, dict) and isinstance(s.get("startTime"), (int, float)) and not isinstance(s.get("startTime"), bool) and math.isfinite(s["startTime"])]
    if not starts:
        return False
    start, finish = execution.started_at.timestamp() * 1_000_000, execution.finished_at.timestamp() * 1_000_000
    # Small explicit clock tolerance; matching ID alone never attests cluster or namespace.
    return start - 5_000_000 <= min(starts) <= finish + 5_000_000


def _candidate(execution, traces, service_map, present, evidence_id):
    matched, gaps = [], []
    for trace_id in execution.trace_ids:
        matches = [t for t in traces if t.get("traceID") == trace_id]
        if len(matches) != 1:
            gaps.append({"traceId": trace_id, "reason": "missing_trace" if not matches else "ambiguous_trace"})
        elif not _trace_matches(matches[0], execution):
            gaps.append({"traceId": trace_id, "reason": "trace_outside_execution_window"})
        else:
            matched.append(matches[0])
    quality = validate_trace_quality(matched)
    usable = [t for t in matched if t["traceID"] not in {q.trace_id for q in quality.results if q.quality.value == "INVALID"}]
    edges, observed, unmapped = defaultdict(list), set(), set()
    def logical(name):
        result = service_map.get(name, name)
        if result in present:
            return result
        unmapped.add(name)
        return None
    for interaction in extract_interactions(parse_spans(usable)):
        for name in interaction.observed_services:
            mapped = logical(name)
            if mapped:
                observed.add(mapped)
        for edge in interaction.edges:
            source, target = logical(edge.source), logical(edge.target)
            if source and target and source != target:
                observed.update((source, target))
                for reference in interaction.edge_evidence.get((edge.source, edge.target), []):
                    edges[source, target].append({**reference, "traceId": interaction.trace_id,
                        "testEvidenceId": evidence_id, "executionId": execution.execution_id,
                        "testName": execution.test_name, "runner": execution.runner,
                        "traceSourceService": edge.source, "traceTargetService": edge.target,
                        "identityBasis": "explicit_mapping" if edge.source in service_map or edge.target in service_map else "exact_name"})
    outcome, assertions, reasons = "indeterminate", [], ["No explicit success condition and outcome trace were supplied."]
    if execution.success_condition is not None:
        condition = SuccessCondition.from_mapping(execution.success_condition)
        outcome_trace = next((t for t in matched if t["traceID"] == execution.outcome_trace_id), None)
        if outcome_trace and validate_trace_quality([outcome_trace]).valid_count == 1:
            status, checks, why = evaluate_trace_success(condition, outcome_trace)
            outcome, assertions, reasons = status.value, [a.to_mapping() for a in checks], list(why)
            # A reported failure or failed assertion must never become an overall passing execution.
            if outcome == "succeeded" and (execution.reported_status != "passed" or any(not a.passed for a in execution.assertions)):
                outcome, reasons = "indeterminate", ["Trace assertions and runner outcome do not establish a consistent successful execution."]
        else:
            reasons = ["The outcome trace is missing, ambiguous, outside the execution window or incomplete."]
    declarations = [d.model_dump(by_alias=True) for d in execution.dependency_declarations]
    candidate = {"candidateId": execution.execution_id, "name": execution.test_name,
        "entryService": execution.entry_service, "services": sorted(observed),
        "traceIds": [t["traceID"] for t in usable], "unmappedServices": sorted(unmapped),
        "traceQuality": {"valid": quality.valid_count, "partial": quality.partial_count, "invalid": quality.invalid_count},
        "edges": [{"source": source, "target": target, "dependency": "Unknown", "evidence": evidence,
                   "declarations": [d for d in declarations if (d["source"], d["target"]) == (source, target)]}
                  for (source, target), evidence in sorted(edges.items())],
        "execution": execution.model_dump(mode="json", by_alias=True),
        "outcome": {"status": outcome, "assertions": assertions, "reasons": reasons,
                    "basis": "trace assertions plus runner report", "experimentallyValidated": False},
        "coverage": {"gaps": gaps, "requested": len(execution.trace_ids), "matched": len(matched)}}
    if not execution.trace_ids:
        candidate["coverage"]["gaps"].append({"reason": "test_runner_integration_required"})
    return candidate


def import_test_evidence(request: TestEvidenceImport, repo=None, *, previous=None):
    repo = repo or management.repository()
    facts = management._bound(request.namespace, request.connection_fingerprint)
    present = {s["name"] for s in facts["services"]}
    if any(e.entry_service not in present for e in request.executions):
        raise ValueError("Each test must name a real entry service in the selected namespace")
    if any(not key.strip() or target not in present for key, target in request.service_map.items()):
        raise ValueError("Explicit trace-service mappings must target discovered Kubernetes services")
    if any(d.source not in present or d.target not in present for e in request.executions for d in e.dependency_declarations):
        raise ValueError("Dependency declarations must name discovered logical Kubernetes services")
    traces = list(request.traces)
    endpoint = approved_jaeger_url(request.jaeger_url) if request.jaeger_url else None
    if endpoint:
        source = JaegerTraceSource.from_url(endpoint)
        missing = [i for e in request.executions for i in e.trace_ids if not any(t.get("traceID") == i for t in traces)]
        def addressed(trace_id):
            try:
                return [t for t in source.fetch_trace(trace_id) if isinstance(t, dict) and t.get("traceID") == trace_id]
            except ConnectionError:
                return []
        # Independent, bounded exact-ID reads. No ambient traffic search or repeated test execution.
        with ThreadPoolExecutor(max_workers=4) as pool:
            for fetched in pool.map(addressed, missing):
                traces.extend(fetched)
    identity = previous["id"] if previous else uuid4().hex
    candidates = [_candidate(e, traces, request.service_map, present, identity) for e in request.executions]
    if previous:
        old = {c["candidateId"]: c for c in previous["candidates"]}
        for candidate in candidates:
            prior = old.get(candidate["candidateId"])
            if not prior:
                continue
            retained = sorted(set(prior["traceIds"]) - set(candidate["traceIds"]))
            candidate["coverage"]["retainedTraceIds"] = retained
            candidate["traceIds"] = sorted(set(prior["traceIds"]) | set(candidate["traceIds"]))
            candidate["services"] = sorted(set(prior["services"]) | set(candidate["services"]))
            merged = {(e["source"], e["target"]): e for e in candidate["edges"]}
            for edge in prior["edges"]:
                pair = edge["source"], edge["target"]
                if pair not in merged:
                    merged[pair] = edge
                else:
                    for reference in edge["evidence"]:
                        if reference not in merged[pair]["evidence"]:
                            merged[pair]["evidence"].append(reference)
            candidate["edges"] = list(merged.values())
            # Current outcome/gaps stay current; historical references do not turn
            # a missing or ambiguous current outcome into a passing test.
    return repo.append("test", identity, previous["version"] if previous else 0, {"namespace": request.namespace,
        "connectionFingerprint": request.connection_fingerprint, "state": "stopped", "source": "application_tests",
        "jaegerUrl": endpoint, "serviceMap": request.service_map, "candidates": candidates,
        "correlation": "Exact execution trace IDs and execution timestamps; imported runner metadata is supplied by the developer. This does not attest cluster/namespace ownership.",
        "limits": management.LIMITS + " Missing/partial traces and unmapped names remain explicit. No call is inferred between separate test requests. Raw trace payloads are not persisted."})


def run_http_test(request: HttpTestRun, repo=None):
    # Validate all targets/contracts before sending any application traffic.
    facts = management._bound(request.namespace, request.connection_fingerprint)
    if not request.test_name.strip():
        raise ValueError("The HTTP probe needs its existing test name")
    if request.entry_service not in {s["name"] for s in facts["services"]}:
        raise ValueError("Choose a discovered entry service")
    if any(not key.strip() or target not in {s["name"] for s in facts["services"]} for key, target in request.service_map.items()):
        raise ValueError("Trace-service mappings must target discovered Kubernetes services")
    endpoint = approved_jaeger_url(request.jaeger_url)
    definition = WorkflowProbeDefinition.from_mapping(request.execution_definition)
    if len(definition.steps) > 10:
        raise ValueError("Run up to 10 HTTP steps at a time")
    run = run_http_workflow_probe(request.application_url, definition, timeout_seconds=3)
    execution = TestExecutionEvidence(test_name=request.test_name, execution_id=uuid4().hex, runner="http_probe",
        application_url=request.application_url, entry_service=request.entry_service, started_at=run.started_at,
        finished_at=run.finished_at, trace_ids=[s.trace_id for s in run.steps], outcome_trace_id=run.trace_id,
        reported_status="passed" if run.business_success else "failed",
        assertions=[{"name": f"{step.name}: {a.name}", "passed": a.passed} for step in run.steps for a in step.assertions],
        success_condition=definition.success_condition.to_mapping())
    return import_test_evidence(TestEvidenceImport(namespace=request.namespace, connection_fingerprint=request.connection_fingerprint,
        executions=[execution], jaeger_url=endpoint, service_map=request.service_map), repo)


def refresh_test_evidence(identity, repo=None):
    """Explicit retry of trace collection only; never rerun an application test."""
    repo = repo or management.repository()
    current = repo.latest("test", identity)
    request = TestEvidenceImport(namespace=current["namespace"], connection_fingerprint=current["connectionFingerprint"],
        executions=[c["execution"] for c in current["candidates"]], jaeger_url=current.get("jaegerUrl"), service_map=current.get("serviceMap", {}))
    return import_test_evidence(request, repo, previous=current)
