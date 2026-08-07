"""Tests for trace_reader.py — Member 1: Workflow Dependency Modeling.

These tests run against the mock Jaeger trace files in sample_data/traces/.
They verify that:
  - files load correctly and return the expected number of raw traces;
  - spans are parsed into typed Span objects;
  - service names are resolved from the process map;
  - parent-child references are correctly resolved;
  - the error tag is detected and mapped to Span.error;
  - root spans (no parent) are correctly identified.

Span counts per file:
  checkout_traces.json: trace-001 (5) + trace-002 (4) + trace-003 (5) = 14 spans
  refund_traces.json:   trace-001 (4) + trace-002 (3) + trace-003 (4) = 11 spans
"""

from pathlib import Path

import pytest

from app.engine.workflow_dependency_modeling.trace_reader import (
    Span,
    load_traces_from_file,
    parse_spans,
)

TRACES_DIR = Path("sample_data/traces")
CHECKOUT_FILE = TRACES_DIR / "checkout_traces.json"
REFUND_FILE = TRACES_DIR / "refund_traces.json"


# ---------------------------------------------------------------------------
# load_traces_from_file
# ---------------------------------------------------------------------------


class TestLoadTracesFromFile:
    def test_checkout_loads_three_traces(self) -> None:
        raw = load_traces_from_file(CHECKOUT_FILE)
        assert len(raw) == 3

    def test_refund_loads_three_traces(self) -> None:
        raw = load_traces_from_file(REFUND_FILE)
        assert len(raw) == 3

    def test_returns_list(self) -> None:
        raw = load_traces_from_file(CHECKOUT_FILE)
        assert isinstance(raw, list)

    def test_each_trace_has_spans_key(self) -> None:
        raw = load_traces_from_file(CHECKOUT_FILE)
        for trace in raw:
            assert "spans" in trace

    def test_each_trace_has_processes_key(self) -> None:
        raw = load_traces_from_file(CHECKOUT_FILE)
        for trace in raw:
            assert "processes" in trace

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_traces_from_file(TRACES_DIR / "does_not_exist.json")


# ---------------------------------------------------------------------------
# parse_spans — checkout
# ---------------------------------------------------------------------------


class TestParseSpansCheckout:
    @pytest.fixture(scope="class")
    def checkout_spans(self) -> list[Span]:
        raw = load_traces_from_file(CHECKOUT_FILE)
        return parse_spans(raw)

    def test_total_span_count(self, checkout_spans: list[Span]) -> None:
        # trace-001: 5 spans, trace-002: 4 spans, trace-003: 5 spans
        assert len(checkout_spans) == 14

    def test_all_items_are_spans(self, checkout_spans: list[Span]) -> None:
        assert all(isinstance(s, Span) for s in checkout_spans)

    def test_service_names_include_core_services(
        self, checkout_spans: list[Span]
    ) -> None:
        names = {s.service_name for s in checkout_spans}
        assert "frontend" in names
        assert "cartservice" in names
        assert "checkoutservice" in names
        assert "paymentservice" in names

    def test_email_service_present(self, checkout_spans: list[Span]) -> None:
        names = {s.service_name for s in checkout_spans}
        assert "emailservice" in names

    def test_three_root_spans(self, checkout_spans: list[Span]) -> None:
        """Each of the 3 traces must have exactly one root span (no parent)."""
        root_spans = [s for s in checkout_spans if s.parent_span_id is None]
        assert len(root_spans) == 3

    def test_root_spans_are_from_frontend(
        self, checkout_spans: list[Span]
    ) -> None:
        root_spans = [s for s in checkout_spans if s.parent_span_id is None]
        assert all(s.service_name == "frontend" for s in root_spans)

    def test_parent_child_link_trace_001(
        self, checkout_spans: list[Span]
    ) -> None:
        """cartservice span in trace-001 must point to frontend's spanID."""
        trace1 = [s for s in checkout_spans if s.trace_id == "trace-checkout-001"]
        frontend = next(s for s in trace1 if s.service_name == "frontend")
        cart = next(s for s in trace1 if s.service_name == "cartservice")
        assert cart.parent_span_id == frontend.span_id

    def test_no_error_spans_in_checkout(
        self, checkout_spans: list[Span]
    ) -> None:
        """All mock checkout traces are successful; no error spans expected."""
        assert all(not s.error for s in checkout_spans)

    def test_duration_positive(self, checkout_spans: list[Span]) -> None:
        assert all(s.duration_us > 0 for s in checkout_spans)

    def test_trace_002_has_no_email_service(
        self, checkout_spans: list[Span]
    ) -> None:
        """trace-002 intentionally omits emailservice (optional-service signal)."""
        trace2 = [s for s in checkout_spans if s.trace_id == "trace-checkout-002"]
        service_names = {s.service_name for s in trace2}
        assert "emailservice" not in service_names

    def test_tags_are_strings(self, checkout_spans: list[Span]) -> None:
        for span in checkout_spans:
            for val in span.tags.values():
                assert isinstance(val, str)


# ---------------------------------------------------------------------------
# parse_spans — refund
# ---------------------------------------------------------------------------


class TestParseSpansRefund:
    @pytest.fixture(scope="class")
    def refund_spans(self) -> list[Span]:
        raw = load_traces_from_file(REFUND_FILE)
        return parse_spans(raw)

    def test_total_span_count(self, refund_spans: list[Span]) -> None:
        # trace-001: 4 spans, trace-002: 3 spans, trace-003: 4 spans
        assert len(refund_spans) == 11

    def test_service_names_include_core_services(
        self, refund_spans: list[Span]
    ) -> None:
        names = {s.service_name for s in refund_spans}
        assert "frontend" in names
        assert "orderservice" in names
        assert "paymentservice" in names

    def test_notification_service_present(self, refund_spans: list[Span]) -> None:
        names = {s.service_name for s in refund_spans}
        assert "notificationservice" in names

    def test_three_root_spans(self, refund_spans: list[Span]) -> None:
        root_spans = [s for s in refund_spans if s.parent_span_id is None]
        assert len(root_spans) == 3

    def test_trace_002_has_no_notification(self, refund_spans: list[Span]) -> None:
        """trace-refund-002 omits notificationservice — optional-service signal."""
        trace2 = [s for s in refund_spans if s.trace_id == "trace-refund-002"]
        service_names = {s.service_name for s in trace2}
        assert "notificationservice" not in service_names
