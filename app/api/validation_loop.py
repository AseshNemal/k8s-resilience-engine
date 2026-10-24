"""Routes of the fault-injection validation loop ("Prove it" on the Recommendations page).

Mounted under ``/api/recommendations/validation-loop`` by ``recommendations.py`` (Member 4's router), so the shared ``app/main.py``
is untouched. The routes only translate: the planning, the one-approval grant, the run, the recovery and the page's view are in
``app.engine.recommendation_evaluation_validation.validation_loop``, and the real cluster pieces are composed in
``validation_loop_environment``. Nothing here can stop a node by itself: only an approved run, started by ``POST /runs`` with the
digest of a plan the person reviewed, does, and only on a cluster that passed every check.

Reads (``GET``) change nothing. ``POST /plan`` and ``POST /probe-test`` read the cluster and send GET requests to the workflow's
entry point; they write nothing.
"""

import os
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.validation_loop_common import (  # noqa: F401  (re-exported for the other modules and the tests)
    CLUSTER_WRITES_ENV,
    _first_kubeconfig,
    _validation_dir,
    require_cluster_writes_armed,
)
from app.api.validation_loop_environment import ClusterProofEnvironment, disposable_environment, docker_desktop_environment
from app.core.config import get_settings
from app.core.database import get_engine
from app.engine.recommendation_evaluation_validation.application import AppliedChangeRepository
from app.engine.recommendation_evaluation_validation.validation.models import ValidationRefusedError
from app.engine.recommendation_evaluation_validation.validation_loop.environment import (
    DISPOSABLE,
    DOCKER_DESKTOP,
    ProbeDefinition,
    ProofEnvironment,
)
from app.engine.recommendation_evaluation_validation.validation_loop.models import Mode
from app.engine.recommendation_evaluation_validation.validation_loop.repository import LoopRepository, RunNotFoundError
from app.engine.recommendation_evaluation_validation.validation_loop.service import (
    ApprovalInput,
    PlanRequest,
    ValidationLoopService,
    proof_from_history,
)
from app.engine.recommendation_evaluation_validation.validation_loop.worker import LoopWorker
from app.shared.evidence_errors import unavailable
from app.shared.schemas.base import APIModel

router = APIRouter()

REFUSAL_STATUS = {"PLAN_CHANGED": 409, "RUN_ACTIVE": 409, "RUN_BLOCKED": 409, "RUN_NOT_RUNNING": 409, "RUN_NOT_RECOVERABLE": 409}
LOOP_BLOCKING = ("PLANNED", "RUNNING", "ABORTING", "NEEDS_MANUAL_RESTORE")


# --- request bodies (Member 4's own; no shared schema changes) -------------------------------------------------------------

class ProbeBody(APIModel):
    url: str
    expected_status: int = 200
    must_contain: list[str] = []
    must_not_contain: list[str] = []


class PlanBody(APIModel):
    target_kind: str = DOCKER_DESKTOP
    mode: str = Mode.QUICK.value
    workflow_id: str
    workflow_source: str = "FROZEN"
    namespace: str
    probe: ProbeBody | None = None
    measure_baseline: bool | None = None
    candidates: int | None = None
    repeats: int | None = None
    smoke_worker: str | None = None


class RunBody(APIModel):
    plan: PlanBody
    plan_digest: str
    acknowledged_workloads: list[str] = []
    desktop_acknowledged: bool = False


class ProbeTestBody(APIModel):
    target_kind: str = DOCKER_DESKTOP
    probe: ProbeBody


# --- wiring (replaced in tests with dependency overrides) -------------------------------------------------------------------

_RUNTIMES: dict[str, tuple[LoopRepository, LoopWorker]] = {}


def get_loop_runtime() -> tuple[LoopRepository, LoopWorker]:
    """The repository and the ONE worker of this backend process. The worker is a singleton so a run's thread outlives a request."""
    url = get_settings().database_url
    if url not in _RUNTIMES:
        engine = get_engine(url)
        repository = LoopRepository(lambda: Session(engine))
        _RUNTIMES[url] = (repository, LoopWorker(repository))
    return _RUNTIMES[url]


class _UnpreparedDisposable:
    """The disposable cluster has no credentials yet. Every read says so, coded; nothing is guessed or substituted."""

    kind = DISPOSABLE

    def __getattr__(self, name):
        def refuse(*args, **kwargs):
            raise unavailable("VALIDATION_CLUSTER_NOT_PREPARED", "The disposable test cluster has not been prepared, so there is nothing "
                              "to prove a change on. Prepare it first, or choose the Docker Desktop cluster.", "validation")
        return refuse

    def requires_desktop_acknowledgement(self) -> bool:
        return False


def get_environment_factory() -> Callable[[str, str], ProofEnvironment]:
    """Builds the environment for a target kind (and a workflow source). Tests substitute it with a fake."""

    def build(kind: str, workflow_source: str = "FROZEN") -> ProofEnvironment:
        recovery = str(_validation_dir() / "recovery")
        if kind == DOCKER_DESKTOP:
            return docker_desktop_environment(_first_kubeconfig(), recovery, workflow_basis=workflow_source)
        if kind == DISPOSABLE:
            kubeconfig = Path(os.environ.get("RESILIENCE_VALIDATION_KUBECONFIG") or _validation_dir() / "kubeconfig")
            if not kubeconfig.is_file():
                return _UnpreparedDisposable()      # type: ignore[return-value]
            return disposable_environment(str(kubeconfig), recovery, workflow_basis=workflow_source)
        raise HTTPException(status_code=422, detail={"code": "UNKNOWN_TARGET", "message": f"{kind!r} is not a target."})

    return build


def get_application_active() -> Callable[[], bool]:
    """Whether a change is being written to the connected cluster, or its pods are still rolling out, right now.

    Either tier, Apply or Reset: the latest stored change is the one the cluster is in, whatever tier made it. A change that is still
    ``APPLYING``, or whose rollout has not completed (``rolling-out``), is in flight, and a test run must not start on top of it.
    """

    def active() -> bool:
        engine = get_engine(get_settings().database_url)
        with Session(engine) as db:
            last = AppliedChangeRepository(db).latest()
            if last is None:
                return False
            return last.status == "APPLYING" or (last.rollout or {}).get("state") == "rolling-out"

    return active


def build_service(kind: str, workflow_source: str, runtime: tuple[LoopRepository, LoopWorker],
                  factory: Callable[[str, str], ProofEnvironment], active: Callable[[], bool]) -> ValidationLoopService:
    repository, worker = runtime
    return ValidationLoopService(factory(kind, workflow_source), repository, worker, application_active=active)


def require_no_validation_run(runtime: tuple[LoopRepository, LoopWorker] = Depends(get_loop_runtime)) -> None:
    """Apply and Reset write to the connected cluster. They must not run while a validation run is using that cluster (or needs a person)."""
    blocking = runtime[0].blocking_run()
    if blocking is not None and blocking["targetKind"] == DOCKER_DESKTOP:
        raise HTTPException(status_code=409, detail={
            "code": "VALIDATION_RUN_ACTIVE",
            "message": f"A validation run ({blocking['status']}) is using this cluster. Wait for it to finish, or recover it, before "
                       "applying or resetting a change."})


def _refused(error: ValidationRefusedError) -> HTTPException:
    return HTTPException(status_code=REFUSAL_STATUS.get(error.code, 422 if error.code.endswith("_INVALID") else 409),
                         detail={"code": error.code, "message": error.message})


def _probe(body: ProbeBody | None) -> ProbeDefinition | None:
    return None if body is None else ProbeDefinition(body.url, body.expected_status, tuple(body.must_contain), tuple(body.must_not_contain))


def _plan_request(body: PlanBody) -> PlanRequest:
    try:
        mode = Mode(body.mode.upper())
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "UNKNOWN_MODE", "message": f"{body.mode!r} is not a validation mode."}) from None
    return PlanRequest(mode=mode, workflow_id=body.workflow_id, namespace=body.namespace, probe=_probe(body.probe),
                       measure_baseline=body.measure_baseline, candidates=body.candidates, repeats=body.repeats,
                       smoke_worker=body.smoke_worker)


def _service(kind: str, source: str, runtime, factory, active) -> ValidationLoopService:
    return build_service(kind, (source or "FROZEN").upper(), runtime, factory, active)


# --- routes ---------------------------------------------------------------------------------------------------------------

@router.get("/targets")
def targets(runtime: tuple[LoopRepository, LoopWorker] = Depends(get_loop_runtime),
            factory: Callable[[str, str], ProofEnvironment] = Depends(get_environment_factory)) -> dict:
    """Which clusters a run could prove a change on, and whether each can be reached. Reads the clusters' identities only."""
    out = []
    for kind, label in ((DOCKER_DESKTOP, "Docker Desktop cluster"), (DISPOSABLE, "Disposable test cluster")):
        entry = {"kind": kind, "label": label, "available": False, "reason": None, "identity": None}
        try:
            identity = factory(kind, "FROZEN").identity()
            entry.update(available=bool(identity.cluster_uid), identity={"name": identity.name, "clusterUid": identity.cluster_uid,
                                                                         "nodes": list(identity.node_names)})
            if not identity.cluster_uid:
                entry["reason"] = "The cluster's identity could not be read."
        except Exception as error:      # noqa: BLE001  (an unreachable or unprepared cluster is a state to show, not a crash)
            entry["reason"] = getattr(error, "message", None) or str(error) or type(error).__name__
        entry["warning"] = ("This is the cluster you use every day. Worker nodes of it will be stopped on purpose." if kind == DOCKER_DESKTOP
                            else "A separate cluster created for tests. Your own cluster is not touched.")
        out.append(entry)
    return {"targets": out, "blockingRun": _brief(runtime[0].blocking_run())}


def _brief(run: dict | None) -> dict | None:
    return None if run is None else {"runKey": run["runKey"], "status": run["status"], "targetKind": run["targetKind"]}


@router.post("/plan")
def plan(body: PlanBody, runtime: tuple[LoopRepository, LoopWorker] = Depends(get_loop_runtime),
         factory: Callable[[str, str], ProofEnvironment] = Depends(get_environment_factory),
         active: Callable[[], bool] = Depends(get_application_active)) -> dict:
    """What a run would do, computed now from the target cluster: candidates, workers, what else would go down, what blocks it."""
    try:
        return _service(body.target_kind, body.workflow_source, runtime, factory, active).plan(_plan_request(body)).to_dict()
    except ValidationRefusedError as error:
        raise _refused(error)


@router.post("/probe-test")
def probe_test(body: ProbeTestBody, runtime: tuple[LoopRepository, LoopWorker] = Depends(get_loop_runtime),
               factory: Callable[[str, str], ProofEnvironment] = Depends(get_environment_factory),
               active: Callable[[], bool] = Depends(get_application_active)) -> dict:
    """Three GET requests to the workflow's entry point, to see that the check passes on the cluster as it is. Changes nothing.

    The address must be this computer's loopback on a port an application is normally published on (see ``allowed_probe_port``); the
    answer says how many requests passed and why one did not, never the page itself.
    """
    try:
        return _service(body.target_kind, "FROZEN", runtime, factory, active).probe_test(_probe(body.probe))      # type: ignore[arg-type]
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"code": "PROBE_URL_NOT_ALLOWED", "message": str(error)}) from error


@router.post("/runs")
def start_run(body: RunBody, _armed: None = Depends(require_cluster_writes_armed), runtime: tuple[LoopRepository, LoopWorker] = Depends(get_loop_runtime),
              factory: Callable[[str, str], ProofEnvironment] = Depends(get_environment_factory),
              active: Callable[[], bool] = Depends(get_application_active)) -> dict:
    """ONE approval for the whole bounded run: the digest of the plan the person reviewed, plus the acknowledgements it asks for.

    The plan is computed again; if anything differs the approval is refused. Nothing is started otherwise.
    """
    try:
        return _service(body.plan.target_kind, body.plan.workflow_source, runtime, factory, active).approve(
            _plan_request(body.plan), ApprovalInput(body.plan_digest, tuple(body.acknowledged_workloads), body.desktop_acknowledged))
    except ValidationRefusedError as error:
        raise _refused(error)


@router.get("/current")
def current(research: bool = Query(False), runtime: tuple[LoopRepository, LoopWorker] = Depends(get_loop_runtime),
            factory: Callable[[str, str], ProofEnvironment] = Depends(get_environment_factory),
            active: Callable[[], bool] = Depends(get_application_active)) -> dict:
    """The latest run as persisted. A backend that restarted mid-run recovers it first (restore and revert only; nothing is resumed)."""
    latest = runtime[0].latest_run()
    kind = latest["targetKind"] if latest else DOCKER_DESKTOP
    return {"run": _service(kind, "FROZEN", runtime, factory, active).current(research=research)}


@router.get("/runs/{run_key}")
def get_run(run_key: str, research: bool = Query(False), runtime: tuple[LoopRepository, LoopWorker] = Depends(get_loop_runtime),
            factory: Callable[[str, str], ProofEnvironment] = Depends(get_environment_factory),
            active: Callable[[], bool] = Depends(get_application_active)) -> dict:
    try:
        run = runtime[0].get_run(run_key)
        return {"run": _service(run["targetKind"], "FROZEN", runtime, factory, active).view(run_key, research=research)}
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": f"No validation run {run_key!r}."})


@router.post("/runs/{run_key}/abort")
def abort(run_key: str, runtime: tuple[LoopRepository, LoopWorker] = Depends(get_loop_runtime),
          factory: Callable[[str, str], ProofEnvironment] = Depends(get_environment_factory),
          active: Callable[[], bool] = Depends(get_application_active)) -> dict:
    """Ask the run to stop at its next safe point. The worker is restarted and the Deployments are put back first."""
    try:
        run = runtime[0].get_run(run_key)
        return {"run": _service(run["targetKind"], "FROZEN", runtime, factory, active).abort(run_key)}
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": f"No validation run {run_key!r}."})
    except ValidationRefusedError as error:
        raise _refused(error)


@router.post("/runs/{run_key}/recover")
def recover(run_key: str, _armed: None = Depends(require_cluster_writes_armed), runtime: tuple[LoopRepository, LoopWorker] = Depends(get_loop_runtime),
            factory: Callable[[str, str], ProofEnvironment] = Depends(get_environment_factory),
            active: Callable[[], bool] = Depends(get_application_active)) -> dict:
    """Retry restoring a run that needs a person: start its workers, put back its Deployments. Never stops a node, never resumes."""
    try:
        run = runtime[0].get_run(run_key)
        return {"run": _service(run["targetKind"], "FROZEN", runtime, factory, active).recover(run_key)}
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": f"No validation run {run_key!r}."})
    except ValidationRefusedError as error:
        raise _refused(error)


@router.get("/history")
def history(limit: int = Query(10, ge=1, le=50), runtime: tuple[LoopRepository, LoopWorker] = Depends(get_loop_runtime)) -> dict:
    """Past runs, as stored. Reads only the database."""
    return {"runs": [{"runKey": r["runKey"], "status": r["status"], "mode": r["mode"], "targetKind": r["targetKind"],
                      "targetName": r["targetName"], "namespace": r["namespace"], "workflowId": r["workflowId"],
                      "createdAt": r["createdAt"], "finishedAt": r["finishedAt"], "winner": (r.get("result") or {}).get("winner")}
                     for r in runtime[0].history(limit)]}


@router.get("/proof")
def proof(namespace: str, workflow_id: str = Query(..., alias="workflowId"),
          candidate_sha256: str | None = Query(None, alias="candidateSha256"),
          runtime: tuple[LoopRepository, LoopWorker] = Depends(get_loop_runtime)) -> dict:
    """For the Approve step: was a change with this exact YAML proven, on which cluster, and at what evidence level. Reads the database only."""
    return proof_from_history(runtime[0], namespace=namespace, workflow_id=workflow_id, candidate_sha256=candidate_sha256)


# The disposable test cluster: assessment, preview of the copy, and creating it. See validation_loop_disposable.py.
from app.api.validation_loop_disposable import router as disposable_router  # noqa: E402  (imports this module's helpers, so it comes last)

router.include_router(disposable_router, prefix="/disposable")
