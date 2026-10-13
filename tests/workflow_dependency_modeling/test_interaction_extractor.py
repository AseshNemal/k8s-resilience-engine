"""Tests for interaction_extractor.py — Member 1: Workflow Dependency Modeling.

Verifies:
  - extract_interactions() produces one TraceInteraction per trace.
  - Correct edge counts per trace (including the optional-service traces).
  - Expected directed edges are present / absent.
  - No intra-service self-loops.
  - aggregate_edges() correctly counts occurrences and computes frequency.
  - Mandatory edges (frequency == 1.0) and optional edges (frequency < 1.0)
    are correctly distinguished.

Edge counts per file:
  checkout:
    trace-001: 4 edges (frontend→cart, cart→checkout, checkout→payment, checkout→email)
    trace-002: 3 edges (no email)
    trace-003: 4 edges
  refund:
    trace-001: 3 edges (frontend→order, order→payment, payment→notification)
    trace-002: 2 edges (no notification)
    trace-003: 3 edges
"""

from pathlib import Path

import pytest

from app.engine.workflow_dependency_modeling.interaction_extractor import (
    ObservedEdge,
    ServiceEdge,
    TraceInteraction,
    aggregate_edges,
    extract_interactions,
)
from app.engine.workflow_dependency_modeling.trace_reader import (
    load_traces_from_file,
    parse_spans,
    Span,
    SpanLink,
)

TRACES_DIR = Path("sample_data/traces")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def checkout_interactions() -> list[TraceInteraction]:
    raw = load_traces_from_file(TRACES_DIR / "checkout_traces.json")
    spans = parse_spans(raw)
    return extract_interactions(spans)


@pytest.fixture(scope="module")
def refund_interactions() -> list[TraceInteraction]:
    raw = load_traces_from_file(TRACES_DIR / "refund_traces.json")
    spans = parse_spans(raw)
    return extract_interactions(spans)


# ---------------------------------------------------------------------------
# extract_interactions — checkout
# ---------------------------------------------------------------------------


class TestExtractInteractionsCheckout:
    def test_produces_three_interactions(
        self, checkout_interactions: list[TraceInteraction]
    ) -> None:
        assert len(checkout_interactions) == 3

    def test_all_are_trace_interaction_objects(
        self, checkout_interactions: list[TraceInteraction]
    ) -> None:
        assert all(isinstance(i, TraceInteraction) for i in checkout_interactions)

    def test_trace_001_has_four_edges(
        self, checkout_interactions: list[TraceInteraction]
    ) -> None:
        trace1 = next(
            i for i in checkout_interactions if i.trace_id == "trace-checkout-001"
        )
        assert len(trace1.edges) == 4

    def test_trace_002_has_three_edges_no_email(
        self, checkout_interactions: list[TraceInteraction]
    ) -> None:
        trace2 = next(
            i for i in checkout_interactions if i.trace_id == "trace-checkout-002"
        )
        assert len(trace2.edges) == 3

    def test_trace_003_has_four_edges(
        self, checkout_interactions: list[TraceInteraction]
    ) -> None:
        trace3 = next(
            i for i in checkout_interactions if i.trace_id == "trace-checkout-003"
        )
        assert len(trace3.edges) == 4

    def test_expected_edges_present_in_trace_001(
        self, checkout_interactions: list[TraceInteraction]
    ) -> None:
        trace1 = next(
            i for i in checkout_interactions if i.trace_id == "trace-checkout-001"
        )
        pairs = {(e.source, e.target) for e in trace1.edges}
        assert ("frontend", "cartservice") in pairs
        assert ("cartservice", "checkoutservice") in pairs
        assert ("checkoutservice", "paymentservice") in pairs
        assert ("checkoutservice", "emailservice") in pairs

    def test_email_edge_absent_in_trace_002(
        self, checkout_interactions: list[TraceInteraction]
    ) -> None:
        trace2 = next(
            i for i in checkout_interactions if i.trace_id == "trace-checkout-002"
        )
        pairs = {(e.source, e.target) for e in trace2.edges}
        assert ("checkoutservice", "emailservice") not in pairs

    def test_no_self_loops_in_any_trace(
        self, checkout_interactions: list[TraceInteraction]
    ) -> None:
        for interaction in checkout_interactions:
            for edge in interaction.edges:
                assert edge.source != edge.target, (
                    f"Self-loop detected: {edge.source} → {edge.target}"
                )

    def test_edges_are_service_edge_objects(
        self, checkout_interactions: list[TraceInteraction]
    ) -> None:
        for interaction in checkout_interactions:
            for edge in interaction.edges:
                assert isinstance(edge, ServiceEdge)

    def test_interactions_sorted_by_trace_id(
        self, checkout_interactions: list[TraceInteraction]
    ) -> None:
        ids = [i.trace_id for i in checkout_interactions]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# extract_interactions — refund
# ---------------------------------------------------------------------------


class TestExtractInteractionsRefund:
    def test_produces_three_interactions(
        self, refund_interactions: list[TraceInteraction]
    ) -> None:
        assert len(refund_interactions) == 3

    def test_trace_001_has_three_edges(
        self, refund_interactions: list[TraceInteraction]
    ) -> None:
        trace1 = next(
            i for i in refund_interactions if i.trace_id == "trace-refund-001"
        )
        assert len(trace1.edges) == 3

    def test_trace_002_has_two_edges_no_notification(
        self, refund_interactions: list[TraceInteraction]
    ) -> None:
        trace2 = next(
            i for i in refund_interactions if i.trace_id == "trace-refund-002"
        )
        assert len(trace2.edges) == 2

    def test_notification_edge_absent_in_trace_002(
        self, refund_interactions: list[TraceInteraction]
    ) -> None:
        trace2 = next(
            i for i in refund_interactions if i.trace_id == "trace-refund-002"
        )
        pairs = {(e.source, e.target) for e in trace2.edges}
        assert ("paymentservice", "notificationservice") not in pairs

    def test_expected_edges_in_trace_001(
        self, refund_interactions: list[TraceInteraction]
    ) -> None:
        trace1 = next(
            i for i in refund_interactions if i.trace_id == "trace-refund-001"
        )
        pairs = {(e.source, e.target) for e in trace1.edges}
        assert ("frontend", "orderservice") in pairs
        assert ("orderservice", "paymentservice") in pairs
        assert ("paymentservice", "notificationservice") in pairs


# ---------------------------------------------------------------------------
# aggregate_edges — checkout
# ---------------------------------------------------------------------------


class TestAggregateEdgesCheckout:
    @pytest.fixture(scope="class")
    def observed(
        self, checkout_interactions: list[TraceInteraction]
    ) -> list[ObservedEdge]:
        return aggregate_edges(checkout_interactions)

    def test_four_unique_edges(self, observed: list[ObservedEdge]) -> None:
        assert len(observed) == 4

    def test_all_are_observed_edge_objects(
        self, observed: list[ObservedEdge]
    ) -> None:
        assert all(isinstance(e, ObservedEdge) for e in observed)

    def test_mandatory_edges_frequency_is_one(
        self, observed: list[ObservedEdge]
    ) -> None:
        by_edge = {(e.source, e.target): e for e in observed}
        assert by_edge[("frontend", "cartservice")].frequency == pytest.approx(1.0)
        assert by_edge[("cartservice", "checkoutservice")].frequency == pytest.approx(
            1.0
        )
        assert by_edge[("checkoutservice", "paymentservice")].frequency == pytest.approx(
            1.0
        )

    def test_mandatory_edges_occurrence_count_is_three(
        self, observed: list[ObservedEdge]
    ) -> None:
        by_edge = {(e.source, e.target): e for e in observed}
        assert by_edge[("frontend", "cartservice")].occurrence_count == 3
        assert by_edge[("cartservice", "checkoutservice")].occurrence_count == 3

    def test_email_edge_frequency_is_two_thirds(
        self, observed: list[ObservedEdge]
    ) -> None:
        by_edge = {(e.source, e.target): e for e in observed}
        email_edge = by_edge[("checkoutservice", "emailservice")]
        assert email_edge.frequency == pytest.approx(2 / 3, abs=0.01)
        assert email_edge.occurrence_count == 2

    def test_sorted_descending_by_frequency(
        self, observed: list[ObservedEdge]
    ) -> None:
        freqs = [e.frequency for e in observed]
        assert freqs == sorted(freqs, reverse=True)


# ---------------------------------------------------------------------------
# aggregate_edges — refund
# ---------------------------------------------------------------------------


class TestAggregateEdgesRefund:
    @pytest.fixture(scope="class")
    def observed(
        self, refund_interactions: list[TraceInteraction]
    ) -> list[ObservedEdge]:
        return aggregate_edges(refund_interactions)

    def test_three_unique_edges(self, observed: list[ObservedEdge]) -> None:
        assert len(observed) == 3

    def test_mandatory_edges_frequency_is_one(
        self, observed: list[ObservedEdge]
    ) -> None:
        by_edge = {(e.source, e.target): e for e in observed}
        assert by_edge[("frontend", "orderservice")].frequency == pytest.approx(1.0)
        assert by_edge[("orderservice", "paymentservice")].frequency == pytest.approx(
            1.0
        )

    def test_notification_edge_frequency_is_two_thirds(
        self, observed: list[ObservedEdge]
    ) -> None:
        by_edge = {(e.source, e.target): e for e in observed}
        notif = by_edge[("paymentservice", "notificationservice")]
        assert notif.frequency == pytest.approx(2 / 3, abs=0.01)
        assert notif.occurrence_count == 2


# ---------------------------------------------------------------------------
# aggregate_edges — edge cases
# ---------------------------------------------------------------------------


class TestAggregateEdgesEdgeCases:
    def test_empty_input_returns_empty_list(self) -> None:
        assert aggregate_edges([]) == []

    def test_single_trace_frequency_is_one(self) -> None:
        interaction = TraceInteraction(
            trace_id="t1",
            edges=[ServiceEdge(source="a", target="b")],
        )
        observed = aggregate_edges([interaction])
        assert len(observed) == 1
        assert observed[0].frequency == pytest.approx(1.0)
        assert observed[0].occurrence_count == 1


def test_span_links_are_preserved_separately_from_synchronous_edges() -> None:
    spans = [
        Span("t1", "producer", None, "orderservice", "publish", 10),
        Span(
            "t1",
            "consumer",
            None,
            "emailservice",
            "consume",
            10,
            links=(SpanLink("t1", "producer", "SPAN_LINK"),),
        ),
    ]
    interaction = extract_interactions(spans)[0]
    assert interaction.edges == []
    assert interaction.linked_edges == [
        ServiceEdge(source="orderservice", target="emailservice")
    ]


def test_retry_attempt_counts_do_not_double_count_client_and_server_spans() -> None:
    spans = [Span("t1", "root", None, "frontend", "checkout", 100)]
    for attempt in ("1", "2"):
        spans.extend(
            [
                Span(
                    "t1",
                    f"client-{attempt}",
                    "root",
                    "frontend",
                    "POST cart",
                    20,
                    tags={"span.kind": "client", "server.address": "cartservice"},
                ),
                Span(
                    "t1",
                    f"server-{attempt}",
                    f"client-{attempt}",
                    "cartservice",
                    "POST cart",
                    15,
                ),
            ]
        )
    interaction = extract_interactions(spans)[0]
    assert interaction.call_counts[("frontend", "cartservice")] == 2
    observed = aggregate_edges([interaction])[0]
    assert observed.occurrence_count == 1
    assert observed.frequency == pytest.approx(1.0)
    assert observed.total_call_count == 2
    assert observed.max_calls_per_trace == 2
    assert observed.repeated_trace_count == 1
