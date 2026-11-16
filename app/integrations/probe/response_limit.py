"""Bound probe evidence while downloading; oversized evidence is a failed check, never a truncated pass."""

import time

import httpx


class ResponseTooLarge(httpx.TransportError):
    """The response exceeded the evidence budget. No partial body may be judged as complete."""


def read_bounded(response: httpx.Response, limit: int, *, timeout_s: float | None = None) -> bytes:
    if limit < 1:
        raise ValueError("Response byte limit must be positive")
    body = bytearray()
    deadline = time.monotonic() + timeout_s if timeout_s is not None else None
    for chunk in response.iter_bytes():
        if deadline is not None and time.monotonic() > deadline:
            raise httpx.ReadTimeout("Probe response exceeded its total time budget")
        if len(body) + len(chunk) > limit:
            raise ResponseTooLarge("Response exceeded the probe evidence limit")
        body.extend(chunk)
    return bytes(body)
