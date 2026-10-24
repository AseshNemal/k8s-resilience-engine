"""The run grant: ONE approval for a bounded run, and the per-step authorizations it is allowed to mint.

A validation run needs many separate authorizations (the apply of each candidate, every controlled node failure, every kill of a
kind worker's container). Asking a person for each of them is not workable, and inventing a person's name to satisfy the field
would be dishonest. So the person approves a whole bounded run once, and that approval is stored as a ``RunGrant``:

* it is bound to the exact plan (``plan_digest``), the target cluster's identity (kube-system UID), the namespace, the workers
  that may be taken down, the candidates and their YAML digests, and the largest number of node failures;
* it carries no name. The ``confirmed_by`` of a minted authorization is ``run-approval:<run>:<digest>``, an identifier of the
  approval that authorizes the step, not a claim about who clicked;
* a ``GrantAuthority`` mints a step authorization only inside those bounds, and counts every node failure against a DURABLE
  budget (``KillBudget``) before anything can be armed. Anything outside the bounds is refused with a code, and an exhausted
  budget stops the run. No check of the per-step mechanism (``ValidationRun``, ``KindWorkerFaultInjector.arm``) is weakened: the
  minted objects are the same ``Authorization``s, bound to the same plan digest, action and target.

Docker Desktop is not reachable through a grant alone: its ``desktop_ack`` only matters together with a
``ResearchClusterAuthorization`` for the exact designated cluster, which the composition layer builds (see ``research_authorization``).
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.engine.recommendation_evaluation_validation.validation.cluster_identity import ResearchClusterAuthorization
from app.engine.recommendation_evaluation_validation.validation.models import (
    APPLY,
    FAIL_BASELINE,
    FAIL_NODE,
    STOP_KIND_WORKER,
    TEST_ENVIRONMENT,
    Authorization,
    TestTarget,
    ValidationRefusedError,
)

RESEARCH_REASON = "The designated Docker Desktop research cluster may have its worker nodes stopped for this run."


@dataclass(frozen=True)
class GrantedCandidate:
    candidate_id: str
    yaml_sha256: str


@dataclass(frozen=True)
class RunGrant:
    """What the person approved. Immutable; stored with the run so the approval can be audited and the run recovered."""

    run_id: str
    plan_digest: str
    target_name: str
    cluster_uid: str
    namespace: str
    workers: tuple[str, ...]
    candidates: tuple[GrantedCandidate, ...]
    repeats: int
    max_failures: int
    measure_baseline: bool
    approved_at: str
    desktop_ack: bool = False         # the person accepted that the Docker Desktop research cluster is the target
    acknowledged_workloads: tuple[str, ...] = ()      # non-critical workloads the person accepted would be disrupted

    @property
    def authorizer(self) -> str:
        """The non-personal identifier written into ``Authorization.confirmed_by``."""
        return f"run-approval:{self.run_id}:{self.plan_digest[:12]}"

    def to_dict(self) -> dict[str, Any]:
        return {"runId": self.run_id, "planDigest": self.plan_digest, "target": self.target_name, "clusterUid": self.cluster_uid,
                "namespace": self.namespace, "workers": list(self.workers),
                "candidates": [{"id": c.candidate_id, "yamlSha256": c.yaml_sha256} for c in self.candidates],
                "repeats": self.repeats, "maxFailures": self.max_failures, "measureBaseline": self.measure_baseline,
                "approvedAt": self.approved_at, "desktopAcknowledged": self.desktop_ack,
                "acknowledgedWorkloads": list(self.acknowledged_workloads)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunGrant":
        return cls(run_id=data["runId"], plan_digest=data["planDigest"], target_name=data["target"],
                   cluster_uid=data["clusterUid"], namespace=data["namespace"], workers=tuple(data["workers"]),
                   candidates=tuple(GrantedCandidate(c["id"], c["yamlSha256"]) for c in data["candidates"]),
                   repeats=int(data["repeats"]), max_failures=int(data["maxFailures"]),
                   measure_baseline=bool(data["measureBaseline"]), approved_at=data["approvedAt"],
                   desktop_ack=bool(data.get("desktopAcknowledged")),
                   acknowledged_workloads=tuple(data.get("acknowledgedWorkloads") or ()))


def plan_digest(parts: dict[str, Any]) -> str:
    """The digest a grant is bound to: canonical JSON of everything the person approved (cluster, nodes, YAMLs, probe, bounds)."""
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def max_failures_for(workers: int, candidates: int, repeats: int, measure_baseline: bool) -> int:
    """The most node failures a plan can cause: the budget a grant is created with."""
    return candidates * workers * repeats + (workers if measure_baseline else 0)


class KillBudget(Protocol):
    """A durable, atomic counter of node failures used against a grant."""

    def reserve(self, run_id: str, count: int, maximum: int) -> bool:
        """Count ``count`` more failures against ``run_id``; False (and no change) when that would pass ``maximum``."""

    def used(self, run_id: str) -> int:
        """Failures counted so far."""


class InMemoryKillBudget:
    """For tests and single-process fakes. The database-backed budget has the same contract and survives a restart."""

    def __init__(self) -> None:
        self._used: dict[str, int] = {}

    def reserve(self, run_id: str, count: int, maximum: int) -> bool:
        if count < 1 or self._used.get(run_id, 0) + count > maximum:
            return False
        self._used[run_id] = self._used.get(run_id, 0) + count
        return True

    def used(self, run_id: str) -> int:
        return self._used.get(run_id, 0)


@dataclass
class GrantAuthority:
    """Mints step authorizations for one run, only inside its grant. Holds no state beyond the durable budget."""

    grant: RunGrant
    budget: KillBudget
    minted: list[tuple[str, str]] = field(default_factory=list)      # (action, subject), for the audit trail

    def _check_target(self, target: TestTarget, plan_digest_value: str) -> None:
        if target.environment != TEST_ENVIRONMENT or target.name != self.grant.target_name:
            raise ValidationRefusedError("GRANT_TARGET_MISMATCH", "The run grant is for a different target than the one in front "
                                         "of the executor, or the target is not a test environment.")
        if plan_digest_value != self.grant.plan_digest:
            raise ValidationRefusedError("GRANT_PLAN_MISMATCH", "The run grant was approved for a different plan.")

    def _mint(self, action: str, subject: str) -> Authorization:
        self.minted.append((action, subject))
        return Authorization(action, self.grant.authorizer, self.grant.plan_digest, self.grant.target_name)

    def authorize_apply(self, target: TestTarget, candidate_id: str, yaml_sha256: str) -> Authorization:
        self._check_target(target, self.grant.plan_digest)
        granted = next((c for c in self.grant.candidates if c.candidate_id == candidate_id), None)
        if granted is None or granted.yaml_sha256 != yaml_sha256:
            raise ValidationRefusedError("GRANT_CANDIDATE_NOT_APPROVED", f"{candidate_id!r} with this YAML was not part of the "
                                         "approved run.")
        return self._mint(APPLY, candidate_id)

    def authorize_failure(self, target: TestTarget, worker: str, *, baseline: bool) -> tuple[Authorization, Authorization]:
        """The pair needed for one controlled failure: the run's own step (FAIL_NODE / FAIL_BASELINE) and the one kill of the container.

        The kill is counted against the durable budget first, so a restart cannot reset it and a crash cannot double-spend it.
        """
        self._check_target(target, self.grant.plan_digest)
        if worker not in self.grant.workers:
            raise ValidationRefusedError("GRANT_WORKER_NOT_APPROVED", f"{worker!r} is not one of the workers this run may stop.")
        if baseline and not self.grant.measure_baseline:
            raise ValidationRefusedError("GRANT_BASELINE_NOT_APPROVED", "A baseline failure was not part of the approved run.")
        if not self.budget.reserve(self.grant.run_id, 1, self.grant.max_failures):
            raise ValidationRefusedError("GRANT_KILL_BUDGET_EXHAUSTED", f"The run is approved for at most {self.grant.max_failures} "
                                         "node failures and has used them.")
        step = self._mint(FAIL_BASELINE if baseline else FAIL_NODE, worker)
        return step, self._mint(STOP_KIND_WORKER, worker)

    def research_authorization(self, cluster_uid: str) -> ResearchClusterAuthorization | None:
        """The authorization that lets the Docker Desktop research cluster be a target, or None when the grant does not carry it.

        It exists only when the person ticked the acknowledgement AND the cluster in front of us is the exact one they approved.
        """
        if not self.grant.desktop_ack or cluster_uid != self.grant.cluster_uid:
            return None
        return ResearchClusterAuthorization(confirmed_by=self.grant.authorizer, cluster_uid=cluster_uid, reason=RESEARCH_REASON)


def new_grant(*, run_id: str, plan_digest_value: str, target_name: str, cluster_uid: str, namespace: str, workers: tuple[str, ...],
              candidates: tuple[GrantedCandidate, ...], repeats: int, measure_baseline: bool, approved_at: datetime,
              desktop_ack: bool = False, acknowledged_workloads: tuple[str, ...] = ()) -> RunGrant:
    """A grant whose failure budget is exactly what its plan can cause."""
    return RunGrant(run_id, plan_digest_value, target_name, cluster_uid, namespace, tuple(workers), tuple(candidates), repeats,
                    max_failures_for(len(workers), len(candidates), repeats, measure_baseline), measure_baseline,
                    approved_at.isoformat(), desktop_ack, tuple(acknowledged_workloads))
