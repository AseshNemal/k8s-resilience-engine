"""An approved change applied to the connected cluster, and the state it replaced."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

APPLYING = "APPLYING"
APPLIED = "APPLIED"
FAILED = "FAILED"              # the apply did not complete and everything it touched was put back
RESET = "RESET"
RESET_FAILED = "RESET_FAILED"  # a restore did not complete; the snapshot is kept and Reset can be tried again
KEPT = "KEPT"                  # a named person decided to keep the change; it no longer blocks another one and can still be reset

# While one of these stands, the cluster is not in the state the person last saw, so nothing else is applied to it.
ACTIVE = frozenset({APPLYING, APPLIED, RESET_FAILED})
RESETTABLE = ACTIVE | {KEPT}
# Still standing in the cluster (applied or kept), whether or not it blocks another change.
STANDING = frozenset({APPLIED, KEPT})


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
    rollout: dict[str, Any] | None = None   # the watched rollout of the latest write (None: predates tracking)
    # One snapshot per Deployment touched, read back straight after the patches were accepted: the values THIS change set.
    # Reset compares the cluster with these before it restores, so a field someone else changed since is never overwritten.
    # None: the change predates this record (or the read-back failed), so what it set is unknown and nothing can be compared.
    after: list[dict[str, Any]] | None = None
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
            "rollout": self.rollout, "after": self.after,
            "events": self.events, "detail": self.detail,
        }
