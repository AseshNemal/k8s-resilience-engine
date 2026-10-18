"""Tests for the error envelope that carries error bodies through the Docker Desktop SDK."""

import json

import pytest
from fastapi.testclient import TestClient

from extension.asgi import application
from extension.error_envelope import ErrorEnvelope

WANT = {"X-Resilience-Envelope": "1"}


@pytest.fixture
def client():
    with TestClient(application) as test_client:  # also exercises the lifespan scope passing through
        yield test_client


def test_an_error_is_returned_as_a_200_envelope_with_the_real_status_and_body(client):
    response = client.get("/api/nope", headers=WANT)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"__resilienceError": True, "status": 404, "body": {"detail": "Not Found"}}


def test_a_validation_error_keeps_its_detail(client):
    response = client.post("/api/recommendations/rec-001/request-approval", json={"nonsense": 1}, headers=WANT)

    envelope = response.json()
    assert response.status_code == 200
    assert envelope["status"] == 422
    assert envelope["body"]["detail"][0]["type"] == "missing"


def test_the_extension_endpoints_report_their_reason_through_the_envelope(client):
    response = client.put("/_extension/cluster", json={"kubeconfig": "not: a kubeconfig"}, headers=WANT)

    envelope = response.json()
    assert envelope["status"] == 422
    assert envelope["body"] == {"detail": "The kubeconfig needs a current-context."}


def test_content_length_matches_the_envelope(client):
    response = client.get("/api/nope", headers=WANT)

    assert int(response.headers["content-length"]) == len(response.content)


def test_successful_responses_are_not_wrapped(client):
    plain = client.get("/api/health")
    wanted = client.get("/api/health", headers=WANT)

    assert wanted.status_code == 200
    assert wanted.json() == plain.json()
    assert "__resilienceError" not in wanted.json()


def test_without_the_header_errors_are_unchanged(client):
    missing = client.get("/api/nope")
    invalid = client.post("/api/recommendations/rec-001/request-approval", json={"nonsense": 1})

    assert missing.status_code == 404
    assert missing.json() == {"detail": "Not Found"}
    assert invalid.status_code == 422


@pytest.mark.parametrize("value", ["0", "true", ""])
def test_only_the_exact_header_value_enables_it(client, value):
    assert client.get("/api/nope", headers={"X-Resilience-Envelope": value}).status_code == 404


async def _call(app, headers):
    """Run a bare ASGI app through the middleware and return (messages sent)."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers}
    await ErrorEnvelope(app)(scope, receive, send)
    return sent


def _plain_app(status, chunks):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"text/plain")]})
        for index, chunk in enumerate(chunks):
            await send({"type": "http.response.body", "body": chunk, "more_body": index < len(chunks) - 1})

    return app


def test_a_non_json_error_body_is_carried_as_text():
    import asyncio

    sent = asyncio.run(_call(_plain_app(500, [b"Internal Server Error"]), [(b"x-resilience-envelope", b"1")]))

    assert sent[0]["status"] == 200
    assert json.loads(sent[1]["body"]) == {"__resilienceError": True, "status": 500, "body": "Internal Server Error"}


def test_an_error_body_sent_in_several_chunks_is_reassembled():
    import asyncio

    sent = asyncio.run(_call(_plain_app(409, [b'{"detail": "Appro', b'val already pending"}']), [(b"x-resilience-envelope", b"1")]))

    assert json.loads(sent[1]["body"])["body"] == {"detail": "Approval already pending"}
    assert len(sent) == 2  # one start, one body: nothing is forwarded before the body is complete


def test_a_successful_streamed_response_is_forwarded_chunk_by_chunk():
    import asyncio

    sent = asyncio.run(_call(_plain_app(200, [b"one", b"two"]), [(b"x-resilience-envelope", b"1")]))

    assert [m.get("body") for m in sent[1:]] == [b"one", b"two"]
    assert sent[0]["status"] == 200
