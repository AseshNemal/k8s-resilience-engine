"""Exhaustive-window discovery, using controlled trace backends rather than a developer cluster."""
import pytest

from app.engine.workflow_dependency_modeling import discovery_scan_service as scans
from tests.workflow_dependency_modeling.test_observed_discovery import request_trace


@pytest.fixture(autouse=True)
def empty_scans():
    scans._scans.clear()
    yield
    scans._scans.clear()


class WindowSource:
    discovery_resolution_us = 1

    def __init__(self, traces, services=None):
        self.traces = traces
        self.services = services or ['web']
        self.calls = []
        self.fail = False

    def list_services(self):
        return self.services

    def fetch_trace_window(self, service, start, end, limit):
        self.calls.append((service, start, end, limit))
        if self.fail:
            raise ConnectionError('private backend error')
        return [trace for trace in self.traces if start <= trace['spans'][0]['startTime'] <= end
                and service in trace['processes']][:limit]


def trace(index, timestamp=None):
    result = request_trace(f't{index}', 'web', f'/route-{index % 3}', 'api')
    result['spans'][0]['startTime'] = timestamp if timestamp is not None else 1000 + index
    return result


def advance(source, **kwargs):
    return scans.advance_discovery_scan(source, binding='approved-source', endpoint='http://traces',
        lookback='1s', page_size=10, now_us=1_000_000, **kwargs)


def exhaust(source):
    report = advance(source)
    for _ in range(2000):
        if report['coverage']['status'] != 'scanning':
            return report
        report = advance(source, scan_id=report['scanId'])
    pytest.fail('Scan did not finish')


def test_collects_more_than_old_total_limit_and_deduplicates_across_services():
    source = WindowSource([trace(i, i * 1000) for i in range(601)], ['web', 'api'])
    report = exhaust(source)
    assert report['coverage']['status'] == 'complete'
    assert report['tracesFetched'] == 601
    assert sum(w['traceCount'] for w in report['workflows']) == 601
    assert report['servicesScanned'] == ['api', 'web']
    assert report['truncated'] is False
    assert len(source.calls) > 2


def test_visits_every_service_including_services_called_by_others():
    source = WindowSource([], [f'service-{i}' for i in range(18)])
    report = exhaust(source)
    assert report['coverage']['servicesCompleted'] == 18
    assert len(source.calls) == 18
    assert not report['servicesSkipped']


def test_fixed_window_does_not_move_between_requests():
    source = WindowSource([trace(i) for i in range(20)])
    first = advance(source)
    second = advance(source, scan_id=first['scanId'])
    assert first['coverage']['windowStartMicros'] == second['coverage']['windowStartMicros'] == 0
    assert first['coverage']['windowEndMicros'] == second['coverage']['windowEndMicros'] == 1_000_000
    assert first['coverage']['queriesCompleted'] == 1
    assert second['coverage']['queriesCompleted'] == 2


def test_full_timestamp_ties_remain_partial_instead_of_skipping_hidden_traces():
    source = WindowSource([trace(i, 500_000) for i in range(30)])
    report = exhaust(source)
    assert report['coverage']['status'] == 'partial'
    assert report['truncated'] is True
    assert report['tracesFetched'] == 10
    assert {gap['reason'] for gap in report['coverage']['unresolvedWindows']} == {'full_batch_at_backend_time_resolution'}


def test_exactly_full_page_is_resolved_by_further_queries_not_assumed_complete():
    source = WindowSource([trace(i, i * 1000) for i in range(10)])
    report = exhaust(source)
    assert report['coverage']['status'] == 'complete'
    assert report['tracesFetched'] == 10
    assert len(source.calls) > 1


def test_backend_failure_preserves_prior_results_and_reports_partial_without_raw_error():
    source = WindowSource([trace(i) for i in range(20)])
    first = advance(source)
    source.fail = True
    report = advance(source, scan_id=first['scanId'])
    assert report['coverage']['status'] == 'partial'
    assert report['tracesFetched'] == first['tracesFetched']
    assert report['workflows'] == first['workflows']
    assert 'private backend' not in str(report)


def test_cancel_stops_future_reads_and_keeps_partial_results():
    source = WindowSource([trace(i) for i in range(20)])
    first = advance(source)
    calls = len(source.calls)
    report = advance(source, scan_id=first['scanId'], cancel=True)
    assert report['coverage']['status'] == 'cancelled'
    assert report['tracesFetched'] == first['tracesFetched']
    assert advance(source, scan_id=first['scanId'])['coverage']['status'] == 'cancelled'
    assert len(source.calls) == calls


def test_unknown_scan_and_changed_connection_cannot_reuse_scan():
    source = WindowSource([])
    with pytest.raises(ValueError, match='expired'):
        advance(source, scan_id='missing')
    first = advance(source)
    scan_id = first['scanId']
    with pytest.raises(ValueError, match='match'):
        scans.advance_discovery_scan(source, binding='different', endpoint='http://other', lookback='1s', scan_id=scan_id)


def test_expired_scan_does_not_return_a_completed_search():
    first = advance(WindowSource([trace(i) for i in range(20)]))
    scans._scans[first['scanId']].touched -= scans._TTL_SECONDS + 1
    empty_source = WindowSource([])
    scan_id = first['scanId']
    with pytest.raises(ValueError, match='expired'):
        advance(empty_source, scan_id=scan_id)


def test_unsupported_adapter_is_explicitly_partial():
    class Unsupported:
        def list_services(self): return ['web']
    report = advance(Unsupported())
    assert report['coverage']['status'] == 'partial'
    assert report['coverage']['unresolvedWindows'][0]['reason'] == 'fixed_window_search_unsupported'


def test_service_listing_failure_is_unavailable_not_empty_or_complete():
    class Down:
        def list_services(self): raise ConnectionError('secret')
    report = advance(Down())
    assert report['state'] == 'jaeger_unavailable'
    assert report['coverage']['status'] == 'partial'
    assert 'secret' not in str(report)


def test_empty_backend_completes_an_empty_search():
    source = WindowSource([])
    source.services = []
    report = exhaust(source)
    assert report['coverage']['status'] == 'complete'
    assert report['workflows'] == []


def test_malformed_batch_is_not_counted_as_usable_evidence():
    class Invalid(WindowSource):
        def fetch_trace_window(self, *_): return [trace(1), {'spans': []}]
    report = advance(Invalid([]))
    assert report['coverage']['status'] == 'partial'
    assert report['tracesFetched'] == 0


def test_no_raw_spans_are_retained_in_scan_state():
    first = advance(WindowSource([trace(1)]))
    state = scans._scans[first['scanId']]
    assert not hasattr(state, 'traces')
    assert 'spans' not in str(state.candidates)


@pytest.mark.parametrize('backend', ['jaeger', 'zipkin', 'tempo'])
def test_live_adapters_send_fixed_time_boundaries_and_query_limits(monkeypatch, backend):
    from app.engine.workflow_dependency_modeling import trace_source as adapters
    calls = []
    def read(url, path, **kwargs):
        calls.append((url, path, kwargs['params']))
        return {'data': []} if backend == 'jaeger' else [] if backend == 'zipkin' else {'traces': []}
    monkeypatch.setattr(adapters, '_http_json', read)
    source = {'jaeger': lambda: adapters.JaegerTraceSource.from_url('http://jaeger'),
              'zipkin': lambda: adapters.ZipkinTraceSource('http://zipkin'),
              'tempo': lambda: adapters.TempoTraceSource('http://tempo')}[backend]()
    assert source.fetch_trace_window('web', 1_000_000, 2_000_000, 50) == []
    params = calls[0][2]
    assert params['limit'] == 50
    if backend == 'jaeger':
        assert (params['start'], params['end']) == (1_000_000, 2_000_000)
    elif backend == 'zipkin':
        assert (params['endTs'], params['lookback']) == (2000, 1000)
    else:
        assert (params['start'], params['end']) == (1, 2)


def test_http_continuation_uses_owning_service_and_validates_connection_binding(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.engine.workflow_dependency_modeling import analysis_session_service as service
    timestamp = scans.time.time_ns() // 1000 - 1_000_000
    source = WindowSource([trace(i, timestamp) for i in range(80)])
    monkeypatch.setattr(service, 'create_trace_source', lambda configuration: source)
    data = {'continuous': True, 'traceSource': {'type': 'jaeger', 'endpoint': 'http://localhost:17686'}, 'lookback': '1h'}
    with TestClient(app) as client:
        first = client.post('/api/workflows/discovery', json=data)
        assert first.status_code == 200
        assert first.json()['coverage']['status'] == 'scanning'
        scan_id = first.json()['scanId']
        changed = client.post('/api/workflows/discovery', json={**data, 'lookback': '2h', 'scanId': scan_id})
        assert changed.status_code == 422
        denied = client.post('/api/workflows/discovery', json={**data, 'traceSource': {'type': 'jaeger', 'endpoint': 'http://not-allowed.example'}, 'scanId': scan_id})
        assert denied.status_code == 422
        stopped = client.post('/api/workflows/discovery', json={**data, 'scanId': scan_id, 'cancel': True})
        assert stopped.status_code == 200
        assert stopped.json()['coverage']['status'] == 'cancelled'
