"""Unit tests for automated root-span workflow discovery (auto_discovery.py)."""

from app.engine.workflow_dependency_modeling.auto_discovery import (
    _slugify,
    discover_root_workflows,
    find_root_spans,
)
from app.engine.workflow_dependency_modeling.trace_reader import (
    Span,
    load_traces_from_file,
)
from app.shared.sample_loader import SAMPLE_DATA_DIR

TRACES_DIR = SAMPLE_DATA_DIR / "traces"


def test_slugify():
    assert _slugify("POST /cart/checkout") == "post_cart_checkout"
    assert _slugify("Checkout-Service.Process") == "checkout_service_process"
    assert _slugify("   ") == "workflow"


def test_find_root_spans():
    s1 = Span(trace_id="t1", span_id="s1", parent_span_id=None, service_name="frontend", operation_name="checkout", duration_us=100)
    s2 = Span(trace_id="t1", span_id="s2", parent_span_id="s1", service_name="cart", operation_name="get_cart", duration_us=50)

    # In trace 2, root has external parent id not in list
    s3 = Span(trace_id="t2", span_id="s3", parent_span_id="ext-1", service_name="frontend", operation_name="view", duration_us=100)
    s4 = Span(trace_id="t2", span_id="s4", parent_span_id="s3", service_name="product", operation_name="get_product", duration_us=50)

    spans_by_trace = {
        "t1": [s1, s2],
        "t2": [s3, s4],
    }

    roots = find_root_spans(spans_by_trace)
    assert roots["t1"] == s1
    assert roots["t2"] == s3


def test_discover_root_workflows_with_checkout_traces():
    traces = load_traces_from_file(TRACES_DIR / "checkout_traces.json")
    discovered = discover_root_workflows(traces)

    assert len(discovered) >= 1
    checkout = discovered[0]
    assert "checkout" in checkout.workflow_id or "checkout" in checkout.operation_name.lower()
    assert checkout.root_service == "frontend"
    assert checkout.trace_count > 0
    assert "paymentservice" in checkout.participating_services
    assert "cartservice" in checkout.participating_services


def test_discover_root_workflows_empty():
    assert discover_root_workflows([]) == []
