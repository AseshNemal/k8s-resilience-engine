"""Real-cluster acceptance support. Never imported by the product or enabled by ordinary tests."""
import json
import os
import time
from pathlib import Path

from sqlalchemy.orm import Session

from scripts.release_acceptance import NAME, NAMESPACE, check


class InterruptedReset(RuntimeError):
    pass


def environment(bundle: Path, uid: str):
    check(bundle, uid)  # read-only identity/designation check before arming this isolated process
    os.environ.update(KUBECONFIG=str(bundle / "kubeconfig"), DATABASE_URL=f"sqlite:///{bundle / 'acceptance.db'}",
                      RESILIENCE_ALLOW_CLUSTER_WRITES="1", RESILIENCE_DECLARED_WORKFLOW_PATH=str(bundle / "declared.json"))


def engine(bundle):
    from app.core.database import get_engine
    return get_engine(f"sqlite:///{bundle / 'acceptance.db'}")


def gateway(bundle, uid, *, mode=None):
    from app.integrations.kubernetes.validation_gateway import KubernetesTestClusterGateway
    from app.integrations.kubernetes.client import ConnectionFailedError
    real = KubernetesTestClusterGateway(str(bundle / "kubeconfig"), allow_writes=True, connected_cluster=True)
    class Guard:
        calls = 0
        def __getattr__(self, name):
            return getattr(real, name)
        def guard(self, namespace, name):
            if real.identity().cluster_uid != uid or namespace != NAMESPACE or name not in {"frontend", "backend"}:
                raise RuntimeError("Acceptance target changed; write refused")
        def patch_deployment(self, doc, **kwargs):
            self.guard(doc["metadata"]["namespace"], doc["metadata"]["name"])
            return real.patch_deployment(doc, **kwargs)
        def restore_deployment(self, snapshot):
            self.guard(snapshot.namespace, snapshot.name)
            self.calls += 1
            if mode == "partial" and self.calls == 2:
                raise ConnectionFailedError("Acceptance transport fault before second restore")
            result = real.restore_deployment(snapshot)
            if mode == "crash":
                (bundle / "crash-after-restore.json").write_text(json.dumps({"deployment": snapshot.name}))
                os._exit(86)  # deliberate child-backend process crash, never a Kubernetes node failure
            return result
    return Guard()


def service(db, gw):
    from app.api.recommendations import get_research_application_service
    from app.engine.recommendation_evaluation_validation.application.live_analyser import ResearchAnalyser
    return get_research_application_service(db, lambda: gw, ResearchAnalyser())


def approved(bundle, db):
    from app.engine.recommendation_evaluation_validation.portfolio_pipeline import build_portfolio_recommendation
    from app.engine.recommendation_evaluation_validation.approval import ApprovalRepository, ResearchApprovalService
    from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
    live = build_portfolio_recommendation(NAMESPACE, declared=True)
    candidate = next((c.id for c in live.report.recommendations if c.feasible and c.type == "COMBINATION"), None)
    assert candidate, "No feasible candidate: preserve evidence; do not bypass the gate"
    proposal = build_yaml_proposal(live, candidate)
    gate = ResearchApprovalService(ApprovalRepository(db))
    operator = json.loads((bundle / "plan.json").read_text())["operator"]
    gate.request_research_approval(candidate, "release-v1", live, acknowledged_by=operator,
        acknowledgement="I understand this workflow is declared by me, not observed, and approve it for research validation only.",
        proposal_yaml=proposal.yaml)
    gate.approve(candidate, "release-v1", "Disposable-cluster release acceptance only", decided_by=operator,
                 evaluate=lambda _: build_portfolio_recommendation(NAMESPACE, declared=True))
    return candidate, operator, lambda _: build_portfolio_recommendation(NAMESPACE, declared=True)


def settled(db, gw, candidate):
    for _ in range(120):
        result = service(db, gw).status(candidate)
        if result["rolloutComplete"]:
            return result
        time.sleep(1)
    raise AssertionError("Rollout did not settle; retain the cluster, snapshots and database for recovery")


def crash_reset(bundle, uid, candidate, operator, application_id):
    environment(Path(bundle), uid)
    with Session(engine(Path(bundle))) as db:
        service(db, gateway(Path(bundle), uid, mode="crash")).reset(candidate, operator,
            expected_application_id=application_id)
