"""Stable, machine-readable errors for "the live evidence needed for this answer is not available".

Every component raises ``EvidenceError`` (or a subclass) instead of substituting sample data, so a
router can turn it into an HTTP error with a code a client or the dashboard can switch on. Messages
are written for display: they never contain credentials, kubeconfig content or raw exception text.
"""

from typing import Any

from fastapi import HTTPException


class EvidenceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int,
        component: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.component = component
        self.retryable = retryable
        self.details = details or {}

    def detail(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "component": self.component,
            "retryable": self.retryable,
        }
        if self.details:
            body["details"] = self.details
        return body

    def to_http(self) -> HTTPException:
        return HTTPException(status_code=self.http_status, detail=self.detail())

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def unavailable(code: str, message: str, component: str, *, http_status: int = 409,
                details: dict[str, Any] | None = None) -> EvidenceError:
    """Shorthand for the common "insufficient evidence" case."""
    return EvidenceError(code, message, http_status=http_status, component=component, details=details)
