"""Where a graph evaluation gets its reference graph from, stated explicitly.

A reference graph (services plus expected edges) is ground truth that a researcher supplies and
reviews. The product has no built-in one: if no source is configured, graph evaluation reports
insufficient evidence instead of scoring against synthetic data. Research code and tests can pass or
configure a ``ReferenceSource`` (for example the synthetic engineering fixtures) and every result then
names the source and whether the reference is synthetic.
"""

from dataclasses import dataclass
from typing import Callable

from app.engine.workflow_dependency_modeling.models import ReferenceWorkflow
from app.shared.evidence_errors import EvidenceError


@dataclass(frozen=True)
class ReferenceSource:
    name: str
    load: Callable[[str], ReferenceWorkflow]


class ReferenceEvidenceUnavailableError(EvidenceError):
    """No usable reference graph exists for the requested evaluation."""

    def __init__(self, message: str) -> None:
        super().__init__(
            "REFERENCE_EVIDENCE_UNAVAILABLE",
            message,
            http_status=409,
            component="workflow_dependency",
        )
