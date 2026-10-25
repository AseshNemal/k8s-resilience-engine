"""Resumable discovery over fixed time windows; no overall trace/service cutoff.

One HTTP request advances one bounded search query. Full windows are bisected
with overlapping boundaries, never advanced past a last-seen timestamp. Thus
timestamp ties cannot be silently skipped. An unsplittable full window stays
partial. Sessions are transient; raw spans are discarded after aggregation.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from threading import Lock
import time
from typing import Any
from uuid import uuid4

from .observed_discovery import _candidates, _excluded_services, lookback_to_ms

_TTL_SECONDS = 3600
_MAX_ACTIVE_SCANS = 4
_registry_lock = Lock()
_scans: dict[str, 'DiscoveryScan'] = {}


@dataclass
class DiscoveryScan:
    binding: str
    endpoint: str
    lookback: str
    page_size: int
    start_us: int
    end_us: int
    services: list[str]
    resolution_us: int = 1
    pending: deque = field(default_factory=deque)
    seen: set[str] = field(default_factory=set)
    candidates: dict = field(default_factory=dict)
    scanned: set[str] = field(default_factory=set)
    gaps: list[dict] = field(default_factory=list)
    state: str = 'scanning'
    requests: int = 0
    touched: float = field(default_factory=time.monotonic)
    lock: Any = field(default_factory=Lock)

    def report(self, scan_id: str) -> dict:
        workflows = deepcopy(sorted(self.candidates.values(), key=lambda c: (-c['traceCount'], c['rootService'], c['operationName'])))
        state = 'discovered' if workflows else 'no_traces'
        if not self.seen and self.gaps and any(g['reason'] in {'service_listing_failed', 'trace_query_failed'} for g in self.gaps):
            state = 'jaeger_unavailable'
        if workflows and all(c['evidenceStatus'] == 'unusable' for c in workflows):
            state = 'incomplete_traces'
        if self.seen and not workflows:
            state = 'no_candidates'
        coverage = {'status': self.state, 'windowStartMicros': self.start_us, 'windowEndMicros': self.end_us,
                    'servicesTotal': len(self.services), 'servicesQueried': len(self.scanned),
                    'servicesCompleted': len(set(self.services) - {item[0] for item in self.pending} - {g['service'] for g in self.gaps}),
                    'queriesCompleted': self.requests, 'pendingWindows': len(self.pending),
                    'queryTimeResolutionMicros': self.resolution_us,
                    'windowBoundsMayRoundOutward': self.resolution_us > 1,
                    'unresolvedWindows': deepcopy(self.gaps),
                    'scope': 'Recorded application services at scan start, within the fixed query window.',
                    'limitations': 'Completion follows backend search responses, not an ingestion snapshot. Sampling, expired traces, late arrivals and backend-side silent truncation can hide evidence.'}
        fingerprint = hashlib.sha256(json.dumps([workflows, coverage], sort_keys=True).encode()).hexdigest()[:16]
        return {'scanId': scan_id, 'coverage': coverage, 'state': state, 'retryable': self.state == 'scanning',
                'message': 'The trace service could not be read.' if state == 'jaeger_unavailable' else f'Found {len(workflows)} observed workflow candidates.',
                'jaegerUrl': self.endpoint, 'lookback': self.lookback,
                'limits': {'tracesPerQuery': self.page_size, 'queriesPerRequest': 1},
                'servicesRecorded': list(self.services), 'servicesScanned': sorted(self.scanned),
                'servicesSkipped': sorted(set(self.services) - self.scanned),
                'servicesUnexamined': sorted(set(self.services) - self.scanned),
                'servicesCalledByOthers': [], 'entryDetection': 'scan_all',
                'fingerprint': fingerprint, 'tracesFetched': len(self.seen),
                'tracesWithCompleteEvidence': sum(c['traceQuality']['validCount'] for c in workflows),
                'truncated': self.state != 'complete', 'workflows': workflows}


def _merge(scan: DiscoveryScan, batch: list[dict]) -> None:
    fresh = []
    batch_ids = set()
    for trace in batch:
        trace_id = trace.get('traceID') if isinstance(trace, dict) else None
        if not isinstance(trace_id, str) or not trace_id:
            raise ConnectionError('Search returned a trace without an identity')
        if trace_id not in scan.seen and trace_id not in batch_ids:
            batch_ids.add(trace_id)
            fresh.append(trace)
    candidates = _candidates(fresh)
    scan.seen.update(batch_ids)
    for candidate in candidates:
        key = (candidate['rootService'], candidate['operationName'])
        old = scan.candidates.get(key)
        if old is None:
            scan.candidates[key] = candidate
            continue
        old['traceCount'] += candidate['traceCount']
        old['participatingServices'] = sorted(set(old['participatingServices']) | set(candidate['participatingServices']))
        old['sampleTraceIds'] = list(dict.fromkeys(old['sampleTraceIds'] + candidate['sampleTraceIds']))[:5]
        for name in ('validCount', 'partialCount', 'invalidCount'):
            old['traceQuality'][name] += candidate['traceQuality'][name]
        valid = old['traceQuality']['validCount']
        old['evidenceStatus'] = 'complete' if valid == old['traceCount'] else 'incomplete' if valid else 'unusable'


def advance_discovery_scan(source, *, binding: str, endpoint: str, lookback: str,
                           page_size: int = 50, scan_id: str | None = None,
                           cancel: bool = False, now_us: int | None = None) -> dict:
    """Start/advance/cancel an opaque scan bound to its approved connection inputs."""
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 1000:
        raise ValueError('Discovery query batch size must be between 1 and 1000')
    with _registry_lock:
        cutoff = time.monotonic() - _TTL_SECONDS
        for key, old in list(_scans.items()):
            if old.touched < cutoff and not old.lock.locked():
                del _scans[key]
        if scan_id is not None:
            scan = _scans.get(scan_id)
            if scan is None or scan.binding != binding:
                raise ValueError('Discovery scan expired or does not match these connection settings; start a new scan')
        else:
            if cancel:
                raise ValueError('Cancelling discovery requires a scanId')
            active = sum(item.state == 'scanning' for item in _scans.values())
            if active >= _MAX_ACTIVE_SCANS:
                raise ValueError('Other discovery scans are active; stop one before starting another')
            # Closed sessions are only useful to clients briefly; bound registry growth.
            for key, old in list(_scans.items()):
                if old.state != 'scanning' and old.touched < time.monotonic() - 60 and not old.lock.locked():
                    del _scans[key]
            scan_id = uuid4().hex
            end = now_us if now_us is not None else time.time_ns() // 1000
            start = end - lookback_to_ms(lookback) * 1000
            scan = DiscoveryScan(binding, endpoint, lookback, page_size, start, end, [])
            scan.resolution_us = getattr(source, 'discovery_resolution_us', 1)
            _scans[scan_id] = scan
    if not scan.lock.acquire(blocking=False):
        raise ValueError('A discovery batch is already running; wait for it before continuing')
    try:
        scan.touched = time.monotonic()
        if cancel:
            if scan.state == 'scanning':
                scan.state = 'cancelled'
            return scan.report(scan_id)
        if scan.state != 'scanning':
            return scan.report(scan_id)
        if not scan.services and not scan.requests:
            try:
                excluded = _excluded_services(None)
                scan.services = sorted({name for name in source.list_services() if isinstance(name, str) and name.strip() and name.lower() not in excluded})
                scan.pending.extend((name, scan.start_us, scan.end_us) for name in scan.services)
            except ConnectionError:
                scan.gaps.append({'service': '*', 'reason': 'service_listing_failed'})
                scan.state = 'partial'
                return scan.report(scan_id)
        if not scan.pending:
            scan.state = 'partial' if scan.gaps else 'complete'
            return scan.report(scan_id)
        service, start, end = scan.pending.popleft()
        query = getattr(source, 'fetch_trace_window', None)
        if query is None:
            scan.gaps.append({'service': service, 'startMicros': start, 'endMicros': end, 'reason': 'fixed_window_search_unsupported'})
            scan.state = 'partial'
            return scan.report(scan_id)
        scan.requests += 1
        scan.scanned.add(service)
        try:
            batch = query(service, start, end, scan.page_size)
            if not isinstance(batch, list):
                raise ConnectionError('Invalid trace search response')
            _merge(scan, batch)
        except (ConnectionError, ValueError, TypeError):
            scan.gaps.append({'service': service, 'startMicros': start, 'endMicros': end, 'reason': 'trace_query_failed'})
            scan.state = 'partial'
            return scan.report(scan_id)
        if len(batch) >= scan.page_size:
            resolution = getattr(source, 'discovery_resolution_us', 1)
            if end - start <= resolution:
                scan.gaps.append({'service': service, 'startMicros': start, 'endMicros': end, 'reason': 'full_batch_at_backend_time_resolution'})
            else:
                # Align the split to the backend's resolution, and overlap at the
                # boundary. Never skip timestamps; seen IDs remove duplicates.
                middle = ((start + end) // 2 // resolution) * resolution
                if not start < middle < end:
                    middle = start + resolution
                scan.pending.appendleft((service, middle, end))
                scan.pending.appendleft((service, start, middle))
        if not scan.pending:
            scan.state = 'partial' if scan.gaps else 'complete'
        return scan.report(scan_id)
    finally:
        scan.touched = time.monotonic()
        scan.lock.release()
