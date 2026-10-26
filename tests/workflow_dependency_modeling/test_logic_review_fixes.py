"""Regression tests for the logic review: whole-window reads, outage traces set aside, held-out run replacement."""

from types import SimpleNamespace

import pytest

from app.engine.workflow_dependency_modeling import held_out_validation as held_out
from app.engine.workflow_dependency_modeling.campaign_execution_service import (
    controlled_outage_trace_ids as collect_outage_trace_ids,
)
from app.engine.workflow_dependency_modeling.execution_models import CorrelationStatus, ExecutionOutcomeStatus
from app.engine.workflow_dependency_modeling.observed_discovery import read_trace_window
from app.engine.workflow_dependency_modeling.service import get_live_evidence


class WindowSource:
    """Traces at fixed timestamps; each query returns at most ``limit`` of those inside [start, end]."""

    discovery_resolution_us = 1

    def __init__(self, stamps):
        self.stamps = stamps
        self.queries = 0

    def fetch_trace_window(self, service_name, start_us, end_us, limit):
        self.queries += 1
        inside = [stamp for stamp in self.stamps if start_us <= stamp <= end_us]
        return [{"traceID": f"t{stamp}", "spans": [], "processes": {}} for stamp in inside[:limit]]


def test_a_full_page_is_split_until_every_trace_in_the_window_is_read():
    now = 10_000_000_000
    source = WindowSource([now - offset for offset in range(1, 2500)])
    traces = read_trace_window(source, "web", "1h", page_size=1000, now_us=now)
    assert len(traces) == 2499 and source.queries > 1


def test_a_window_that_cannot_be_read_completely_is_refused():
    now = 10_000_000_000
    with pytest.raises(ValueError, match="shorter time window"):
        read_trace_window(WindowSource([now - offset for offset in range(1, 5000)]), "web", "1h", page_size=10,
                          max_queries=4, now_us=now)


def _root_trace(trace_id, *, partial=False):
    tags = [{"key": "http.method", "value": "POST"}, {"key": "http.target", "value": "/orders"},
            {"key": "span.kind", "value": "server"}]
    spans = [{"traceID": trace_id, "spanID": f"{trace_id}r", "operationName": "web", "references": [], "startTime": 1,
              "duration": 9, "processID": "p1", "tags": tags},
             {"traceID": trace_id, "spanID": f"{trace_id}c", "operationName": "call orders", "startTime": 2, "duration": 5,
              "processID": "p1", "references": [{"refType": "CHILD_OF", "traceID": trace_id, "spanID": f"{trace_id}r"}],
              "tags": [{"key": "span.kind", "value": "client"}, {"key": "net.peer.name", "value": "orders"}]
              + ([{"key": "error", "value": True}] if partial else [])}]
    if not partial:
        spans.append({"traceID": trace_id, "spanID": f"{trace_id}s", "operationName": "Create", "startTime": 3, "duration": 2,
                      "processID": "p2", "references": [{"refType": "CHILD_OF", "traceID": trace_id, "spanID": f"{trace_id}c"}],
                      "tags": [{"key": "span.kind", "value": "server"}]})
    processes = {"p1": {"serviceName": "web"}} if partial else {"p1": {"serviceName": "web"}, "p2": {"serviceName": "orders"}}
    return {"traceID": trace_id, "spans": spans, "processes": processes}


class ListSource:
    def __init__(self, traces):
        self.traces = traces

    def fetch_trace_window(self, service_name, start_us, end_us, limit):
        return self.traces


def test_traces_of_recorded_outage_runs_are_set_aside_not_judged_broken():
    traces = [_root_trace("a1"), _root_trace("a2"), _root_trace("outage1", partial=True)]
    evidence = get_live_evidence("web", "POST /orders", "http://jaeger.test", 1000, "1h", ListSource(traces),
                                 set_aside_trace_ids=frozenset({"outage1"}))
    assert evidence["setAsideOutageTraceCount"] == 1
    assert evidence["traceCount"] == 2 and evidence["traceQuality"]["excludedCount"] == 0
    unfiltered = get_live_evidence("web", "POST /orders", "http://jaeger.test", 1000, "1h", ListSource(traces))
    assert unfiltered["traceQuality"]["excludedCount"] == 1


def test_outage_trace_ids_come_from_outage_batches_and_verified_assertions_only():
    def outcome(batch, ids, assertion=None):
        return SimpleNamespace(registration=SimpleNamespace(batch_id=batch), trace_ids=frozenset(ids),
                               assertions=[SimpleNamespace(name=assertion)] if assertion else [])

    repository = SimpleNamespace(list=lambda *args, **kwargs: [
        outcome("batch:x:baseline", {"b1", "b2"}),
        outcome("batch:x:intervention:cart", {"o1", "o1-prep"}),
        outcome("campaign:x:heldout:abc:s01", {"h1"}),
        outcome("batch:manual", {"m1"}, assertion="verified_unavailable_service:email"),
    ])
    assert collect_outage_trace_ids(repository) == frozenset({"o1", "o1-prep", "h1", "m1"})


def test_a_held_out_scenario_replaces_runs_excluded_for_incomplete_evidence(monkeypatch):
    recorded = []
    outcomes = iter([ExecutionOutcomeStatus.INDETERMINATE] + [ExecutionOutcomeStatus.FAILED] * 10)

    def record(campaign, analysis, batch_id, *args):
        index = len(recorded)
        recorded.append(SimpleNamespace(
            registration=SimpleNamespace(execution_id=f"e{index}", expected_trace_id=f"t{index}"),
            trace_ids=frozenset({f"t{index}"}), matched_trace_id=f"t{index}",
            correlation_status=CorrelationStatus.MATCHED, status=next(outcomes)))

    campaign = SimpleNamespace(baseline=SimpleNamespace(required_trials=3, batch_id="base"), analysis_session_id="a")
    cluster = SimpleNamespace(require_opt_in=lambda: None, interrupted=lambda: [],
                              inspect=lambda name: SimpleNamespace(down=True))
    monkeypatch.setattr(held_out, "get_experiment_campaign", lambda campaign_id: campaign)
    monkeypatch.setattr(held_out, "baseline_readiness", lambda *args: {"ready": True})
    monkeypatch.setattr(held_out, "intervention_status", lambda campaign_id: {"services": []})
    monkeypatch.setattr(held_out, "get_workflow_analysis", lambda session_id: None)
    monkeypatch.setattr(held_out, "outage_scaler", lambda: cluster)
    monkeypatch.setattr(held_out, "take_down_verified", lambda cluster, services: ["down"])
    monkeypatch.setattr(held_out, "restore_verified", lambda cluster, services: ["up"])
    monkeypatch.setattr(held_out, "_record_campaign_trial", record)
    monkeypatch.setattr(held_out, "list_executions", lambda batch_id, repository=None: list(recorded) if batch_id != "base" else [])
    monkeypatch.setattr(held_out, "campaign_policy", lambda campaign_id: held_out.AssessmentPolicy(min_samples_per_cohort=3))
    from app.engine.workflow_dependency_modeling.held_out_validation_repository import InMemoryHeldOutValidationRepository

    held_out.run_held_out_scenario("campaign:x", "heldout:r", {"scenarioId": "s01", "services": ["a", "b"]},
                                   "http://app.test", actor="tester", repository=InMemoryHeldOutValidationRepository())
    assert len(recorded) == 4          # one indeterminate run replaced: three usable runs
