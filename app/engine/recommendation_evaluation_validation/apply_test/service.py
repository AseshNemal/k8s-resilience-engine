"""One explicit Apply action, durably tracked through rollout, measurements and a report.

No node failure is injected. Functional HTTP tests and measured resource usage are separate from Member 3's
model recalculation. A crash never replays Apply; the persisted application still exposes its Reset snapshot.
"""
import threading
import time
import uuid
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.engine.recommendation_evaluation_validation.application import AppliedChangeRepository, ApplicationRefused, ApplicationService
from app.engine.recommendation_evaluation_validation.approval import (
    ApprovalModel, ApprovalRepository, DECLARED_WORKFLOW_SOURCE, LIVE_EVIDENCE_SOURCE,
    evaluate_eligibility, has_research_provenance, has_verified_provenance,
)
from app.engine.recommendation_evaluation_validation.approval.research import RESEARCH_TIER, declaration_digest
from app.engine.recommendation_evaluation_validation.apply_test.measurements import measure_workflows
from app.engine.recommendation_evaluation_validation.apply_test.repository import RunRepository, now
from app.engine.recommendation_evaluation_validation.yaml_proposal import GENERATED, build_yaml_proposal
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.approval import ApprovalStatus
from app.integrations.kubernetes.quantity import parse_quantity

_LOCK = threading.Lock()
_THREADS = {}
TERMINAL = {"COMPLETED", "COMPLETED_WITH_GAPS", "TESTS_FAILED", "FAILED", "INTERRUPTED"}
ACTOR = "Explicit Apply action"


def _sampled_pod_usage(snapshot):
    """Sum only the recorded pod samples. Unknown or missing samples never become zero."""
    pods = ((snapshot or {}).get("resources") or {}).get("pods") or []
    usage = [container.get("usage") or {} for pod in pods for container in pod.get("containers") or []]
    def total(resource, factor):
        values = [parse_quantity(row.get(resource)) for row in usage]
        if not values or any(v is None or not math.isfinite(v) or v < 0 for v in values):
            return None
        return sum(values) * factor
    return {"sampledPods": len(pods), "cpuMillicores": total("cpu", 1000), "memoryMiB": total("memory", 1 / 1048576)}


def _risk_target_met(report):
    predicted = report.get("predictedRisk")
    observed = (((report.get("riskAnalysis") or {}).get("observed")) or {}).get("blastRadius")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (predicted, observed)):
        return None
    return observed <= predicted + 1e-6


class ApplyTestService:
    def __init__(self, engine, gateway_factory, loader, application_factory, *, writes_enabled,
                 rollout_timeout=600, poll_seconds=2, sleep=time.sleep, measure=measure_workflows):
        self.repo = RunRepository(engine)
        self.engine, self.gateway_factory = engine, gateway_factory
        self.loader, self.application_factory = loader, application_factory
        self.writes_enabled = writes_enabled
        self.timeout, self.poll, self.sleep, self.measure = rollout_timeout, poll_seconds, sleep, measure

    def start(self, *, candidate_id, namespace, declared, fingerprint, yaml_sha256, request_key, background=True):
        if not self.writes_enabled:
            raise ApplicationRefused("CLUSTER_WRITES_NOT_ENABLED", "Apply is not enabled by this backend's deployment.", http_status=403)
        existing = self.repo.get(request_key=request_key)
        identity_key = [candidate_id, namespace, declared, fingerprint, yaml_sha256]
        if existing:
            if existing["selectionKey"] != identity_key:
                raise ApplicationRefused("REQUEST_KEY_REUSED", "This request key belongs to a different selection.")
            return self.read(existing["runId"])
        identity = self.gateway_factory().identity()
        if not identity.cluster_uid:
            raise ApplicationRefused("CLUSTER_IDENTITY_UNKNOWN", "The connected cluster UID could not be verified. The extension must be able to read the kube-system namespace. Open Connect cluster and set up this cluster again to review and update its access.")
        with Session(self.engine) as db:     # each cluster is independent: a change left on another one blocks nothing here
            if any(ApplicationService._cluster_match(c, identity.cluster_uid) != "other" for c in AppliedChangeRepository(db).active()):
                raise ApplicationRefused("CHANGE_ACTIVE", "A change is already applied or awaiting recovery. Reset it before starting another run.")
        run_id = "apply-test-" + uuid.uuid4().hex
        payload = {"candidateId": candidate_id, "namespace": namespace, "declared": declared,
                   "selectionKey": identity_key, "expectedFingerprint": fingerprint, "expectedYamlSha256": yaml_sha256,
                   "clusterUid": identity.cluster_uid, "clusterName": identity.context,
                   "events": [], "report": {"label": "Apply and functional test report", "faultInjected": False,
                    "riskMeaning": "Risk is recalculated by the model over observed placement; functional checks are measured separately."}}
        with _LOCK:
            try:
                self.repo.create(run_id, request_key, payload)
            except IntegrityError:
                existing = self.repo.get(request_key=request_key)
                if existing and existing["selectionKey"] == identity_key:
                    return self.read(existing["runId"])
                raise ApplicationRefused("RUN_ACTIVE", "An Apply and test run is already active.") from None
            if background:
                thread = threading.Thread(target=self.execute, args=(run_id,), daemon=True, name=run_id)
                _THREADS[run_id] = thread
                thread.start()
        if not background:
            self.execute(run_id)
        return self.read(run_id)

    def _connected_uid(self):
        """The connected cluster's UID, or None when it cannot be read just now. None is unknown, never "a different cluster"."""
        try:
            return getattr(self.gateway_factory().identity(), "cluster_uid", None) or None
        except Exception:     # not connected, unreachable, credential renewing: the cluster is unknown, not another one
            return None

    def _event(self, run_id, phase, command, message):
        self.repo.update(run_id, event={"phase": phase, "command": command, "message": message})

    def _identity(self, run):
        gateway = self.gateway_factory()
        uid = gateway.identity().cluster_uid
        if not uid:
            raise ApplicationRefused("CLUSTER_IDENTITY_UNKNOWN", "The connected cluster could not be identified just now (its connection may still be renewing). The run stopped without writing.", http_status=503, retryable=True)
        if uid != run["clusterUid"]:
            raise ApplicationRefused("CLUSTER_CHANGED", "The connected cluster changed. The run stopped without writing to the new cluster.")
        return gateway

    def _load(self, run):
        self._identity(run)
        live = self.loader(run["namespace"], declared=run["declared"])
        self._identity(run)
        return live

    def _checked(self, run):
        live = self._load(run)
        standing = evaluate_eligibility(live, run["candidateId"])
        proposal = build_yaml_proposal(live, run["candidateId"])
        expected_source = DECLARED_WORKFLOW_SOURCE if run["declared"] else LIVE_EVIDENCE_SOURCE
        if (live.evidence.get("source") != expected_source or not standing.eligible or proposal.status != GENERATED
            or standing.fingerprint != run["expectedFingerprint"] or proposal.sha256 != run["expectedYamlSha256"]
            or (not run["declared"] and not proposal.applicable)):
            raise ApplicationRefused("SELECTION_STALE", "The selected candidate or generated YAML changed. Refresh the candidates before Apply.")
        return live, proposal, standing

    def _consent(self, db, run, live, proposal, standing):
        """Record the actual Apply action, not a fabricated named-person approval or a typed acknowledgement.

        The legacy application service consumes this immutable, server-generated authorization. No separate user approval
        screen is required. Its target, eligibility, fingerprint, YAML integrity and Reset checks remain unchanged.
        """
        provenance = {**live.evidence, "applyConsent": {"action": "apply-and-test", "at": now().isoformat(), "identityVerified": False}}
        if run["declared"]:
            if declaration_digest(live.evidence) is None:
                raise ApplicationRefused("DECLARATION_UNVERIFIED", "The declaration digest is missing.")
            provenance.update(researchTier=RESEARCH_TIER, researchAcknowledgement={
                "by": ACTOR, "statement": "The explicit Apply action starts this selected research proposal and its functional checks; workflow definitions are declared, not observed.",
                "source": "apply-action", "at": now().isoformat()})
        at = now()
        approval = ApprovalModel(id=0, recommendation_id=run["candidateId"], proposal_version=run["runId"],
            status=ApprovalStatus.APPROVED, requested_at=at, requested_by=ACTOR, decided_at=at, decided_by=ACTOR,
            decision_note="Explicit Apply action; no separate approval workflow.",
            recommendation_snapshot=next(c.model_dump() for c in live.report.recommendations if c.id == run["candidateId"]),
            yaml_snapshot=proposal.yaml, evaluation_snapshot={"provenance": provenance,
                "proposal": {"fingerprint": standing.fingerprint, "failedNode": live.evidence["failedNode"],
                    "yaml": {"origin": "server-generated", "sha256": proposal.sha256, "verifiedAgainstRecommendation": True}},
                "approvalCheck": {"proposalFingerprint": standing.fingerprint, "authorization": "explicit-apply-action"}})
        if not (has_research_provenance(approval) if run["declared"] else has_verified_provenance(approval)):
            raise ApplicationRefused("EVIDENCE_UNVERIFIED", "The Apply action has no verified evaluation provenance.")
        return ApprovalRepository(db).save(approval)

    def _snapshot(self, run, live, phase):
        def progress(command):
            self._identity(run)
            self._event(run["runId"], phase, command, "Checking the configured workflow response.")
        workflows = self.measure(live, on_check=progress)
        self._event(run["runId"], phase, "GET metrics.k8s.io nodes and namespace pods", "Measuring CPU and memory usage; requests are not usage.")
        resources = self._identity(run).read_resource_usage(run["namespace"])
        return {"capturedAt": now().isoformat(), "workflows": workflows, "resources": resources}

    def execute(self, run_id):
        run = self.repo.get(run_id)
        report = dict(run["report"])
        try:
            self.repo.update(run_id, status="RUNNING")
            self._event(run_id, "baseline", "Read live topology and evaluate the selected candidate", "Recording the baseline before any change.")
            live, proposal, standing = self._checked(run)
            report.update(baselineRisk=live.details.get("baselineImpact"),
                candidate=next(c.model_dump(mode="json") for c in live.report.recommendations if c.id == run["candidateId"]),
                workflowSetDigest=live.evidence.get("workflowSetDigest"),
                predictedRisk=next(c.predicted_blast_radius for c in live.report.recommendations if c.id == run["candidateId"]),
                before=self._snapshot(run, live, "baseline"), yaml=proposal.yaml, yamlSha256=proposal.sha256,
                workflowBasis=live.evidence.get("workflowBasis"), workflows=live.evidence.get("workflows"))
            self.repo.update(run_id, report=report)
            self._event(run_id, "applying", "Validate and PATCH the selected Deployments through Kubernetes API", "Saving the previous state, then applying only the selected changes.")
            with Session(self.engine) as db:
                approval = self._consent(db, run, live, proposal, standing)
                self.repo.update(run_id, authorizationId=approval.id)
                service = self.application_factory(db, run["declared"])
                # Recheck after baseline measurements; Apply is never based on a now-stale preview.
                change = service.apply(run["candidateId"], ACTOR, evaluate=lambda _: self._checked(run)[0])
                self.repo.update(run_id, applicationId=change.application_id)
            deadline = time.monotonic() + self.timeout
            self._event(run_id, "rollout", "GET Deployment rollout status", "Waiting for the new generation and ready replicas.")
            while True:
                self._identity(run)
                with Session(self.engine) as db:
                    application = self.application_factory(db, run["declared"])
                    # Watch only the rollout here. The immutable analysis plan was saved before Apply;
                    # execute it below, after the functional checks, while the UI says Analysis.
                    application.analyser = None
                    status = application.status(run["candidateId"])
                self.repo.update(run_id, applicationStatus=status)
                rollout = status.get("rollout") or {}
                if rollout.get("state") == "completed":
                    break
                if time.monotonic() >= deadline or rollout.get("state") in ("blocked", "failed", "timed-out"):
                    raise ApplicationRefused("ROLLOUT_NOT_COMPLETED", "The rollout did not complete. The stored snapshot remains available for Reset.")
                self._event(run_id, "rollout", "GET Deployment rollout status", "Still waiting for the selected Deployments.")
                self.sleep(self.poll)
            self._event(run_id, "testing", "Read current topology; GET configured workflow checks", "Testing the rolled-out application and collecting resource usage.")
            after = self._load(run)
            if after.evidence.get("workflowSetDigest") != live.evidence.get("workflowSetDigest"):
                raise ApplicationRefused("WORKFLOW_CHANGED", "Workflow definitions changed during the run; results cannot be compared.")
            report["after"] = self._snapshot(run, after, "testing")
            self._event(run_id, "analysis", "Member 3 failure-impact recalculation and before/after comparison", "Separating measured workflow results from model-based risk.")
            self._identity(run)
            with Session(self.engine) as db:
                status = self.application_factory(db, run["declared"]).status(run["candidateId"])
            report["riskAnalysis"] = status.get("analysis", {}).get("apply")
            report["riskTargetMet"] = _risk_target_met(report)
            rows = report["after"]["workflows"]["rows"]
            failed = any(r["httpStatus"] == "FAILED" or r["readiness"] == "NOT_READY" for r in rows)
            complete = all(report[phase]["workflows"]["complete"] and report[phase]["resources"]["available"] for phase in ("before", "after")) and (
                (report["riskAnalysis"] or {}).get("status") == "COMPLETED")
            result = "TESTS_FAILED" if failed else "COMPLETED" if complete else "COMPLETED_WITH_GAPS"
            report.update(outcome=result, finishedAt=now().isoformat(), application=change.to_dict())
            self._event(run_id, "report", "Write the persisted report", "Finished. Missing measurements remain explicitly unavailable.")
            self.repo.update(run_id, report=report, status=result)
        except Exception as error:
            detail = error.detail() if isinstance(error, EvidenceError) else {"code": "APPLY_TEST_ERROR", "message": "The run stopped unexpectedly. Inspect the saved progress and Reset if a change was applied."}
            report.update(outcome="FAILED", error=detail, finishedAt=now().isoformat())
            self._event(run_id, "failed", "Run stopped", detail["message"])
            self.repo.update(run_id, status="FAILED", report=report)
        finally:
            with _LOCK:
                _THREADS.pop(run_id, None)

    def read(self, run_id=None):
        run = self.repo.get(run_id)
        if run is None:
            return None
        thread = _THREADS.get(run["runId"])
        updated = datetime.fromisoformat(run["updatedAt"])
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if run["status"] not in TERMINAL and not (thread and thread.is_alive()) and now() - updated > timedelta(seconds=120):
            self.repo.update(run["runId"], status="INTERRUPTED", event={"phase": "interrupted", "command": "No command replayed",
                "message": "The background run was interrupted. Apply will not be repeated; inspect the application and use Reset if needed."})
            run = self.repo.get(run["runId"])
        run["report"] = {**(run.get("report") or {}), "riskTargetMet": _risk_target_met(run.get("report") or {})}
        with Session(self.engine) as db:
            change = AppliedChangeRepository(db).latest_for(run["candidateId"])
            # A crash can happen after the snapshot is saved but before the worker records applicationId.
            if change and change.approval_id == run.get("authorizationId"):
                run["application"] = change.to_dict()
                match = ApplicationService._cluster_match(change, self._connected_uid())
                run["clusterMatch"] = match       # "same" | "other" | "unknown": the stored change against the cluster connected now
                run["changeCluster"] = ({"context": change.cluster.get("context"), "apiServer": change.cluster.get("apiServer")}
                                        if match == "other" else None)
                run["canReset"] = (change.status in ("APPLYING", "APPLIED", "RESET_FAILED") and run["status"] in TERMINAL
                                   and match != "other")
        return run

    def live(self, run_id=None):
        """What the cluster is doing for this run right now: each target Deployment's replicas and each pod's node and state.

        Read only, and never replayed into a write. It says why it has nothing to show (no change applied yet, another cluster
        connected, cluster unreadable) instead of showing an empty table.
        """
        run = self.read(run_id)
        if run is None:
            return None
        current = (run.get("events") or [{}])[-1] if run.get("events") else {}
        base = {"runId": run["runId"], "runStatus": run["status"], "phase": current.get("phase"), "message": current.get("message"),
                "capturedAt": now().isoformat(), "namespace": run.get("namespace")}
        names = list((run.get("application") or {}).get("deployments") or [])
        if not names:
            return {**base, "available": False, "reason": "NOTHING_APPLIED_YET",
                    "message": "No Deployment has been changed yet; the live view starts when Apply patches them."}
        if run.get("clusterMatch") == "other":
            return {**base, "available": False, "reason": "OTHER_CLUSTER",
                    "message": "This run belongs to another cluster than the one connected now."}
        try:
            deployments = self.gateway_factory().read_live_activity(run["namespace"], names)
        except Exception:        # the cluster could not be read just now: say so, never show the last picture as current
            return {**base, "available": False, "reason": "CLUSTER_UNREADABLE",
                    "message": "The cluster could not be read just now. This view refreshes on its own."}
        per_node: dict[str, int] = {}
        for deployment in deployments:
            for pod in deployment["pods"]:
                if pod.get("node"):
                    per_node[pod["node"]] = per_node.get(pod["node"], 0) + 1
        return {**base, "available": True, "deployments": deployments,
                "nodes": [{"name": n, "pods": c} for n, c in sorted(per_node.items())]}

    def history(self, *, limit=20, offset=0):
        rows, more = self.repo.history(limit=limit, offset=offset)
        summaries = []
        for run in rows:
            report = run.get("report") or {}
            summaries.append({**{key: run.get(key) for key in (
                "runId", "candidateId", "namespace", "clusterUid", "clusterName", "declared", "createdAt", "status")},
                "candidate": report.get("candidate"), "workflowSetDigest": report.get("workflowSetDigest"),
                "yamlSha256": report.get("yamlSha256") or run.get("expectedYamlSha256"),
                "riskTargetMet": _risk_target_met(report),
                "baselineRisk": (report.get("baselineRisk") or {}).get("blastRadius"),
                "predictedRisk": report.get("predictedRisk"),
                "afterRisk": ((report.get("riskAnalysis") or {}).get("observed") or {}).get("blastRadius"),
                "beforeWorkflows": (report.get("before") or {}).get("workflows"),
                "afterWorkflows": (report.get("after") or {}).get("workflows"),
                "beforePodUsage": _sampled_pod_usage(report.get("before")),
                "afterPodUsage": _sampled_pod_usage(report.get("after")),
                "beforeResources": ((report.get("before") or {}).get("resources") or {}).get("available"),
                "afterResources": ((report.get("after") or {}).get("resources") or {}).get("available")})
        return {"runs": summaries, "hasMore": more, "offset": offset}
