"""The probe's HTTP fetch: loopback only, GET only, and a failed request is data rather than an exception."""

import httpx
import pytest

from app.engine.recommendation_evaluation_validation.validation.workflow_probe import ProbeSpec, WorkflowProbe
from app.integrations.probe.http_fetch import LoopbackHttpFetch, require_loopback


def spec(url="http://127.0.0.1:18080/productpage", **options):
    return ProbeSpec(workflow_id="w", url=url, required_text=("Book Details",), forbidden_text=("currently unavailable",),
                     min_samples=1, **options)


def test_only_the_loopback_address_is_reachable():
    for url in ("http://127.0.0.1:18080/p", "http://localhost:18080/p", "http://[::1]:18080/p"):
        assert require_loopback(url) == url
    for url in ("http://10.0.0.5/p", "http://example.com/p", "http://192.168.1.2:80", "ftp://127.0.0.1/p", "", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            require_loopback(url)


def test_a_remote_host_is_refused_before_any_request_is_made():
    sent = []
    fetch = LoopbackHttpFetch(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)))

    with pytest.raises(ValueError):
        fetch(spec("http://example.com/p"))

    assert sent == []


def test_a_good_page_comes_back_with_status_body_and_latency():
    fetch = LoopbackHttpFetch(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="Book Details here")))

    got = fetch(spec())

    assert got.status == 200 and "Book Details" in got.body and got.error is None and got.latency_ms >= 0


def test_a_redirect_is_not_followed_and_only_a_get_is_sent():
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url)))
        return httpx.Response(302, headers={"location": "http://example.com/elsewhere"})

    got = LoopbackHttpFetch(transport=httpx.MockTransport(handler))(spec())

    assert got.status == 302 and seen == [("GET", "http://127.0.0.1:18080/productpage")]


def test_a_connection_that_fails_is_a_failed_sample_named_by_its_error_type():
    def refuse(request):
        raise httpx.ConnectError("refused")

    got = LoopbackHttpFetch(transport=httpx.MockTransport(refuse))(spec())

    assert got.status is None and got.error == "ConnectError"


def test_the_body_is_capped():
    big = LoopbackHttpFetch(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="x" * 5000)), max_body_bytes=100)

    assert len(big(spec()).body) == 100


def test_it_plugs_into_the_engines_probe_and_judges_a_dependency_failure_page_as_a_failure():
    pages = iter(["Book Details Book Reviews", "Book Details Sorry, product reviews are currently unavailable for this book."])
    fetch = LoopbackHttpFetch(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=next(pages))))
    probe = WorkflowProbe(spec(), fetch)

    first, second = probe.sample(), probe.sample()

    assert first.ok is True and second.ok is False and "currently unavailable" in second.reason
