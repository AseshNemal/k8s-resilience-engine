"""An approved change applied to the connected cluster, and the state it replaced."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

APPLYING = "APPLYING"
APPLIED = "APPLIED"
FAILED = "FAILED"              # the apply did not complete and everything it touched was put back
RESET = "RESET"
RESET_FAILED = "RESET_FAILED"  # a restore did not complete; the snapshot is kept and Reset can be tried again

# While one of these stands, the cluster is not in the state the person last saw, so nothing else is applied to it.
ACTIVE = frozenset({APPLYING, APPLIED, RESET_FAILED})
RESETTABLE = ACTIVE


@dataclass
class AppliedChange:
    application_id: str
    recommendation_id: str
    approval_id: int
    tier: str                     # "production" | "research"
    namespace: str
    status: str
    yaml_applied: str
    yaml_sha256: str
    before: list[dict[str, Any]]  # one snapshot per Deployment touched, as read just before the first write
    cluster: dict[str, Any]
    applied_by: str
    applied_at: datetime
    events: list[dict[str, Any]] = field(default_factory=list)
    detail: dict[str, Any] | None = None
    reset_by: str | None = None
    reset_at: datetime | None = None
    id: int | None = None

    @property
    def deployments(self) -> list[str]:
        return [snapshot["name"] for snapshot in self.before]

    def to_dict(self) -> dict[str, Any]:
        return {
            "applicationId": self.application_id, "recommendationId": self.recommendation_id, "approvalId": self.approval_id,
            "tier": self.tier, "namespace": self.namespace, "status": self.status, "deployments": self.deployments,
            "yaml": self.yaml_applied, "yamlSha256": self.yaml_sha256, "before": self.before, "cluster": self.cluster,
            "appliedBy": self.applied_by, "appliedAt": self.applied_at.isoformat(),
            "resetBy": self.reset_by, "resetAt": self.reset_at.isoformat() if self.reset_at else None,
            "events": self.events, "detail": self.detail,
        }
