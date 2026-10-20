"""Carry HTTP error responses through the Docker Desktop SDK without losing their bodies.

When the extension UI calls the backend, the SDK turns any non-2xx answer into a thrown error that
holds only a generic status line (for example "HTTP 422: Unprocessable Entity") and drops the
response body, so messages such as "This credential can change the cluster" never reach the page.

A request that sets the header below gets its error answer back as a normal 200 response whose
JSON body is {"__resilienceError": true, "status": <real status>, "body": <original body>}; the
UI's fetch adapter (extension/ui-src/shim.js) unwraps it into the real status and body. Requests
without the header, and every successful response, are passed through untouched, so the
application's API contract is unchanged for every other client.
"""

import json

ENVELOPE_HEADER = b"x-resilience-envelope"


class ErrorEnvelope:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        wanted = scope["type"] == "http" and any(
            name.lower() == ENVELOPE_HEADER and value == b"1" for name, value in scope["headers"]
        )
        if not wanted:
            await self.app(scope, receive, send)
            return

        status = None
        chunks: list[bytes] = []

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                if message["status"] < 400:
                    status = 0  # success: stream through unchanged
                    await send(message)
                else:
                    status = message["status"]  # error: hold back until the body is complete
                return
            if message["type"] != "http.response.body" or status is None or status == 0:
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json"), (b"content-length", b"%d" % len(payload := _envelope(status, b"".join(chunks))))],
            })
            await send({"type": "http.response.body", "body": payload})

        await self.app(scope, receive, capture)


def _envelope(status: int, raw: bytes) -> bytes:
    try:
        body = json.loads(raw)
    except ValueError:
        body = raw.decode("utf-8", "replace")
    return json.dumps({"__resilienceError": True, "status": status, "body": body}).encode("utf-8")
