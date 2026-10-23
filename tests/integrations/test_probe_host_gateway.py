"""A loopback URL given by the developer is reached through the container's route to the host, and only a loopback URL is."""

import httpx
import pytest

from app.integrations.probe.http_fetch import LoopbackHttpFetch, through_host_gateway
from app.engine.recommendation_evaluation_validation.validation.workflow_probe import ProbeSpec


def spec(url="http://127.0.0.1:18080/productpage?x=1"):
    return ProbeSpec(workflow_id="wf", url=url)


def test_the_host_is_replaced_by_the_gateway_and_the_port_path_and_query_are_kept():
    assert through_host_gateway("http://127.0.0.1:18080/productpage?x=1", "host.docker.internal") == \
        "http://host.docker.internal:18080/productpage?x=1"
    assert through_host_gateway("http://localhost/a", "host.docker.internal") == "http://host.docker.internal/a"
    assert through_host_gateway("http://127.0.0.1:18080/a", None) == "http://127.0.0.1:18080/a"


def test_the_request_goes_to_the_gateway_but_only_after_the_loopback_check_passed():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, text="ok")

    fetch = LoopbackHttpFetch(transport=httpx.MockTransport(handler), host_gateway="host.docker.internal")

    assert fetch(spec()).status == 200 and seen == ["http://host.docker.internal:18080/productpage?x=1"]
    with pytest.raises(ValueError):
        fetch(spec("http://example.com/productpage"))
    assert len(seen) == 1


def test_the_gateway_can_come_from_the_backends_environment(monkeypatch):
    monkeypatch.setenv("RESILIENCE_PROBE_HOST_GATEWAY", "host.docker.internal")
    seen = []
    fetch = LoopbackHttpFetch(transport=httpx.MockTransport(lambda r: seen.append(str(r.url)) or httpx.Response(200, text="ok")))

    fetch(spec())

    assert seen[0].startswith("http://host.docker.internal:18080")
