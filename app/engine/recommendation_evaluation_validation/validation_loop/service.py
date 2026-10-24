"""The validation loop as the page uses it: plan, approve once, watch, abort, recover, and read the proof.

Planning reads the target and computes everything again, every time: the target's own topology, the recommendation on it, the
predictions for each worker, what else on the cluster a worker's loss would take down, whether the workflow check passes. Approving
is a second, separate act that carries the digest of the plan the person saw. The service recomputes the plan and refuses if
anything differs ("the plan changed"), so an approval is always for exactly what is about to happen. The approval is one grant for
the whole bounded run; it carries no name.

The service never talks to a cluster itself: the ``ProofEnvironment`` does. It never decides a run's safety either: the loop, the
grant, the disruption guard and the executor do.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.engine.recommendation_evaluation_validation.validation.models import ValidationRefusedError
from app.engine.recommendation_evaluation_validation.validation_loop.disruption import make_guard
from app.engine.recommendation_evaluation_validation.validation_loop.environment import (
    DOCKER_DESKTOP,
    ProbeDefinition,
    ProofEnvironment,
)
from app.engine.recommendation_evaluation_validation.validation_loop.grant import (
    GrantAuthority,
    GrantedCandidate,
    new_grant,
)
from app.engine.recommendation_evaluation_validation.validation_loop.loop import (
    LoopObserver,
    ValidationLoop,
    observed_estimate,
)
from app.engine.recommendation_evaluation_validation.validation_loop.models import LoopSettings, Mode, ModePlan, resolve_mode
from app.engine.recommendation_evaluation_validation.validation_loop.planning import (
    Issue,
    LoopPlan,
    digest_parts,
    disruption_for,
    plan_digest,
    select_candidates,
)
from app.engine.recommendation_evaluation_validation.validation_loop.repository import LoopRepository
from app.engine.recommendation_evaluation_validation.validation_loop.view import build_view
from app.engine.recommendation_evaluation_validation.validation_loop.worker import LoopWorker


@dataclass
class PlanRequest:
    mode: Mode
    workflow_id: str
    namespace: str
    probe: ProbeDefinition | None = None
    measure_baseline: bool | None = None
    candidates: int | None = None
    repeats: int | None = None
    smoke_worker: str | None = None


@dataclass
class ApprovalInput:
    plan_digest: str
    acknowledged_workloads: tuple[str, ...] = ()
    desktop_acknowledged: bool = False


class ValidationLoopService:
    def __init__(self, environment: ProofEnvironment, repository: LoopRepository, worker: LoopWorker, *,
                 settings: LoopSettings | None = None, application_active: Callable[[], bool] = lambda: False,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._env, self._repo, self._worker = environment, repository, worker
        self._settings, self._application_active, self._clock = settings or LoopSettings(), application_active, clock

    # -- planning ------------------------------------------------------------------------------------------------

    def plan(self, request: PlanRequest) -> LoopPlan:
        identity = self._env.identity()
        issues: list[Issue] = []
        blocking_run = self._repo.blocking_run()
        if blocking_run is not None:
            issues.append(Issue("RUN_BLOCKED", f"Run {blocking_run['runKey']} is {blocking_run['status']}. It has to finish, or be "
                                "recovered, before another run can start."))
        if identity.kind == DOCKER_DESKTOP and self._application_active():
            issues.append(Issue("APPLICATION_ACTIVE", "A change is being applied to or reset on this cluster. Wait for it to finish; "
                                "a test run and an Apply would write to the same Deployments."))
        if not identity.cluster_uid:
            issues.append(Issue("CLUSTER_IDENTITY_UNVERIFIED", "The identity of the cluster (the UID of its kube-system namespace) "
                                "could not be read, so it is not assumed to be the one you mean."))

        loaded = self._env.load(request.workflow_id, request.namespace)       # refuses with a coded error when it cannot
        graph = loaded.graph
        workers = self._env.eligible_workers(graph)
        riskiest = (loaded.live.evidence or {}).get("failedNode")
        if not workers:
            issues.append(Issue("NO_ELIGIBLE_WORKER", "No worker node is Ready and eligible, so no node failure can be tested."))
            mode_plan = ModePlan(request.mode, 1, (), 1, False)
        else:
            mode_plan = resolve_mode(request.mode, workers, riskiest_worker=riskiest if riskiest in workers else None,
                                     smoke_worker=request.smoke_worker, candidates=request.candidates, repeats=request.repeats,
                                     measure_baseline=request.measure_baseline, settings=self._settings)

        candidates, skipped = ([], [])
        if mode_plan.workers:
            candidates, skipped = select_candidates(loaded.live, loaded.workflow, list(mode_plan.workers), mode_plan.candidates)
        if mode_plan.workers and not candidates:
            issues.append(Issue("NO_PROVABLE_CANDIDATE", "No candidate change is both feasible and ranked on this cluster, so there is "
                                "nothing to prove."))
        if candidates and len(candidates) < mode_plan.candidates:
            issues.append(Issue("FEWER_CANDIDATES", f"{len(candidates)} of the {mode_plan.candidates} candidates asked for can be proven.",
                                blocking=False))

        disruption = {}
        if mode_plan.workers:
            try:
                disruption = disruption_for(list(mode_plan.workers), self._env.read_pods(), request.namespace,
                                            self._settings.critical_namespaces)
            except Exception as error:      # noqa: BLE001  (fail closed: unknown disruption is not "none")
                issues.append(Issue("EVIDENCE_INCOMPLETE", f"What else runs on the workers could not be read: {error}"))
        for worker, report in disruption.items():
            for finding in report.blocks:
                issues.append(Issue("ESSENTIAL_WORKLOAD", f"Stopping {worker} would make {finding.workload} unavailable "
                                    f"({finding.reason})."))
            for problem in report.problems:
                issues.append(Issue("EVIDENCE_INCOMPLETE", problem))

        readiness = None
        if mode_plan.workers:
            readiness = self._env.fault_readiness(list(mode_plan.workers), self._repo.window_verified(identity.cluster_uid or ""))
            if readiness is not None:
                if not readiness.identifiable or not readiness.controllable or readiness.window == "UNREADABLE":
                    issues.append(Issue("WORKERS_NOT_CONTROLLABLE", "The worker containers cannot all be identified as kind workers of this "
                                        "cluster, found running, and inspected. No node failure will be attempted."))
                elif readiness.smoke_only and request.mode is not Mode.SMOKE:
                    issues.append(Issue("SMOKE_TEST_FIRST", "Docker may restart a stopped worker by itself on this cluster. Run one supervised "
                                        "Smoke Test first; a larger run is possible once a failure has been observed to stay down."))

        parts = digest_parts(identity=identity, namespace=request.namespace, workflow_id=request.workflow_id, mode_plan=mode_plan,
                             candidates=candidates, probe=request.probe, disruption=disruption,
                             critical_namespaces=self._settings.critical_namespaces)
        plan = LoopPlan(mode_plan, identity, request.namespace, request.workflow_id, loaded.workflow_basis, request.probe, candidates,
                        disruption, readiness, issues, observed_estimate(self._repo.observed_durations(), mode_plan.planned_failures())
                        if self._repo.observed_durations() else None,
                        plan_digest(parts), riskiest, skipped, list(loaded.notes))
        if request.probe is None:
            plan.notes.append("No workflow check was given: a candidate passes on Kubernetes readiness alone (evidence level L1).")
        if mode_plan.preliminary:
            plan.notes.append("This mode gives preliminary evidence. Full Research Validation repeats each test for reproducible results.")
        return plan

    def probe_test(self, probe: ProbeDefinition) -> dict[str, Any]:
        """Three samples of the workflow check against the cluster as it is now. Sends GET requests only."""
        return self._env.probe_check(probe)

    # -- approving and starting ----------------------------------------------------------------------------------

    def approve(self, request: PlanRequest, approval: ApprovalInput) -> dict[str, Any]:
        plan = self.plan(request)
        if plan.digest != approval.plan_digest:
            raise ValidationRefusedError("PLAN_CHANGED", "The plan changed since you reviewed it (the cluster, a candidate or what is "
                                         "running on a worker). Review the new plan and approve again.")
        if not plan.can_approve:
            raise ValidationRefusedError("PLAN_BLOCKED", "; ".join(f"{i.code}: {i.message}" for i in plan.blocking))
        missing = sorted(set(plan.acknowledgements_needed) - set(approval.acknowledged_workloads))
        if missing:
            raise ValidationRefusedError("ACKNOWLEDGEMENT_MISSING", "These workloads would go down and have not been acknowledged: "
                                         + ", ".join(missing))
        if self._env.requires_desktop_acknowledgement() and not approval.desktop_acknowledged:
            raise ValidationRefusedError("DESKTOP_ACKNOWLEDGEMENT_REQUIRED", "This is your Docker Desktop research cluster. Worker nodes "
                                         "will be stopped on it; confirm that this is intended.")
        if plan.probe is not None:
            probe = self._env.probe_check(plan.probe)
            if probe.get("survives") is not True:
                raise ValidationRefusedError("PROBE_FAILED", "The workflow check does not pass on the cluster as it is now, so a failure "
                                             "could not be told from an existing problem: " + "; ".join(probe.get("reasons") or ["no samples"]))

        identity, mode_plan = plan.identity, plan.mode_plan
        grant = new_grant(
            run_id=f"loop-{uuid.uuid4().hex[:12]}", plan_digest_value=plan.digest, target_name=_target_name(identity),
            cluster_uid=identity.cluster_uid or "", namespace=plan.namespace, workers=mode_plan.workers,
            candidates=tuple(GrantedCandidate(c.loop_candidate.candidate_id, c.loop_candidate.yaml_sha256) for c in plan.candidates),
            repeats=mode_plan.repeats, measure_baseline=mode_plan.measure_baseline, approved_at=self._clock(),
            desktop_ack=approval.desktop_acknowledged, acknowledged_workloads=tuple(approval.acknowledged_workloads))
        loaded = self._env.load(request.workflow_id, request.namespace)
        self._repo.create_run(grant, mode=mode_plan.mode.value, target_kind=identity.kind, workflow_id=plan.workflow_id,
                              plan=plan.to_dict())
        candidates = [c.loop_candidate for c in plan.candidates]
        settings = self._settings

        def build(observer: LoopObserver, abort: Callable[[], bool]) -> ValidationLoop:
            authority = GrantAuthority(grant, self._repo)
            guard = make_guard(read_pods=self._env.read_pods, read_graph=lambda: self._env.read_graph(plan.namespace),
                               namespace=plan.namespace, critical_namespaces=settings.critical_namespaces,
                               acknowledged=frozenset(grant.acknowledged_workloads))
            factory = self._env.executor_factory(grant, authority, plan.probe,
                                                 lambda points: self._repo.save_restore_points(grant.run_id, points))
            return ValidationLoop(workflow=loaded.workflow, plan=mode_plan, authority=authority, candidates=candidates,
                                  executor_factory=factory, probe_required=plan.probe is not None,
                                  arm=self._env.arm(grant, authority), before_kill=guard, node_ready=self._env.node_ready,
                                  abort=abort, observer=observer)

        self._worker.start(grant.run_id, build, self._env.recoverer(self._repo.get_run(grant.run_id)))
        return self.view(grant.run_id)

    # -- watching, aborting, recovering --------------------------------------------------------------------------

    def view(self, run_key: str, *, research: bool = False) -> dict[str, Any]:
        run = self._repo.get_run(run_key)
        return build_view(run, self._repo.experiments(run_key), self._repo.observed_durations(), now=self._clock(),
                          worker_alive=self._worker.is_working(run_key), research=research)

    def current(self, *, research: bool = False) -> dict[str, Any] | None:
        self.ensure_recovered()
        run = self._repo.latest_run()
        return None if run is None else self.view(run["runKey"], research=research)

    def history(self, limit: int = 10) -> list[dict[str, Any]]:
        return [{"runKey": r["runKey"], "status": r["status"], "mode": r["mode"], "targetName": r["targetName"],
                 "namespace": r["namespace"], "workflowId": r["workflowId"], "createdAt": r["createdAt"],
                 "finishedAt": r["finishedAt"], "winner": (r.get("result") or {}).get("winner")} for r in self._repo.history(limit)]

    def abort(self, run_key: str) -> dict[str, Any]:
        if not self._worker.abort(run_key):
            raise ValidationRefusedError("RUN_NOT_RUNNING", "That run is not running, so there is nothing to abort.")
        return self.view(run_key)

    def recover(self, run_key: str) -> dict[str, Any]:
        """Retry the recovery of a run that needs a person. Restores and reverts only; never stops a node, never resumes."""
        run = self._repo.get_run(run_key)
        if run["status"] != "NEEDS_MANUAL_RESTORE" or self._worker.is_working(run_key):
            raise ValidationRefusedError("RUN_NOT_RECOVERABLE", "Only a run that needs manual restoration can be recovered here.")
        self._worker.recover_run(run_key, self._env.recoverer(run))
        return self.view(run_key)

    def ensure_recovered(self) -> list[str]:
        """On the first call after a backend start: recover runs left live by a previous process. Restore and revert only."""
        return self._worker.reconcile(self._env.recoverer)

    def proof_for(self, *, namespace: str, workflow_id: str, candidate_sha256: str | None) -> dict[str, Any]:
        return proof_from_history(self._repo, namespace=namespace, workflow_id=workflow_id, candidate_sha256=candidate_sha256)


def proof_from_history(repository: LoopRepository, *, namespace: str, workflow_id: str, candidate_sha256: str | None) -> dict[str, Any]:
    """What the Approve step shows: has a candidate with this exact YAML been proven, on which cluster, at what evidence level.

    Reads only the database. A proof stays attached to the cluster it was obtained on: it is shown with that cluster's name and kind,
    and it never stands for an approval of anything.
    """
    for run in repository.history(20):
        result = run.get("result") or {}
        if run["namespace"] != namespace or run["workflowId"] != workflow_id or run["status"] != "COMPLETED":
            continue
        winner = result.get("winner")
        granted = {c["id"]: c["yamlSha256"] for c in (run.get("approval") or {}).get("candidates") or []}
        state = "PROVEN" if winner and candidate_sha256 and granted.get(winner) == candidate_sha256 else ("OTHER_PROVEN" if winner else "NOT_PROVEN")
        return {"state": state, "runKey": run["runKey"], "target": {"kind": run["targetKind"], "name": run["targetName"]},
                "mode": run["mode"], "preliminary": (run.get("plan") or {}).get("mode", {}).get("preliminary"),
                "winner": winner, "finishedAt": run["finishedAt"]}
    return {"state": "NOT_RUN"}


def _target_name(identity) -> str:
    return identity.name
