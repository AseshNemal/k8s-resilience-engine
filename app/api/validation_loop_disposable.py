"""The disposable test cluster: can it be created, what would be copied onto it, and creating that copy. Routes of the validation loop.

Three steps, each its own request, none automatic:

* ``GET /assessment``: the memory assessment, from what the restricted Docker proxy reports. Only a SUFFICIENT verdict says a cluster can be
  created; every other verdict says what to change and creates nothing. The cluster itself is created by the host helper on this machine
  (``kind``), after an approval of its own, never from here.
* ``POST /preview``: what would be copied from the application's namespace (or pasted by the person), as portable manifests, and what could
  not be copied (Secrets above all). Reads the connected cluster; writes nothing.
* ``POST /deploy``: creates the previewed objects on the disposable cluster, only with the digest of the preview the person saw, only when the
  copy is deployable, only on a cluster that is NOT the connected one, and only when this deployment armed writes.
"""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.validation_loop_common import _first_kubeconfig, _validation_dir, require_cluster_writes_armed
from app.engine.recommendation_evaluation_validation.validation_loop.preparation import (
    PreparationPlan,
    PreparationRefused,
    parse_manual,
    prepare,
)
from app.engine.recommendation_evaluation_validation.validation_loop.resources import PROFILES, advice, assess, facts_from_engine, merge_samples
from app.integrations.docker.engine import DockerEngineFacts
from app.integrations.kubernetes.client import ClusterAccessError
from app.integrations.kubernetes.validation_gateway import AmbientCredentialsError, KubernetesTestClusterGateway
from app.shared.schemas.base import APIModel

router = APIRouter()
DEFAULT_COPY_NAMESPACE = "resilience-validation-app"
SAMPLES = 3


class PreviewBody(APIModel):
    namespace: str
    target_namespace: str = DEFAULT_COPY_NAMESPACE
    manual_yaml: str | None = None


class DeployBody(PreviewBody):
    preview_digest: str


def get_docker_facts() -> DockerEngineFacts:
    """Read-only facts about the Docker VM, through the restricted proxy. Tests substitute it."""
    return DockerEngineFacts()


def get_source_gateway() -> Callable[[], KubernetesTestClusterGateway]:
    """The cluster the application runs on (the connected one), read only."""

    def build() -> KubernetesTestClusterGateway:
        try:
            return KubernetesTestClusterGateway(_first_kubeconfig(), connected_cluster=True)
        except AmbientCredentialsError:
            raise HTTPException(status_code=409, detail={"code": "NOT_CONNECTED", "message": "No cluster is connected."}) from None

    return build


def get_target_gateway() -> Callable[[], KubernetesTestClusterGateway]:
    """The disposable cluster, with credentials of its own, able to create the copy. Never the connected cluster's credential."""

    def build() -> KubernetesTestClusterGateway:
        path = Path(os.environ.get("RESILIENCE_VALIDATION_KUBECONFIG") or _validation_dir() / "kubeconfig")
        if not path.is_file():
            raise HTTPException(status_code=409, detail={"code": "VALIDATION_CLUSTER_NOT_PREPARED",
                                                         "message": "The disposable test cluster has not been prepared."})
        return KubernetesTestClusterGateway(str(path), allow_writes=True)

    return build


@router.get("/assessment")
def assessment(profile: str = Query("minimal"), app_memory_mib: float | None = Query(None, alias="appMemoryMiB"),
               facts: DockerEngineFacts = Depends(get_docker_facts)) -> dict[str, Any]:
    """Can this machine hold a disposable cluster? Reads the Docker VM's memory and each container's use, three times, and counts each at its largest."""
    if profile not in PROFILES:
        raise HTTPException(status_code=422, detail={"code": "UNKNOWN_PROFILE", "message": f"{profile!r} is not a profile."})
    try:
        info = facts.info()
        readings = [facts_from_engine(info, facts.container_memory()) for _ in range(SAMPLES)]
    except Exception as error:      # noqa: BLE001  (Docker unreachable is a state with a reason, never a guess)
        return {"verdict": "UNKNOWN", "canCreate": False, "reasons": [f"Docker could not be read through the proxy: {type(error).__name__}"],
                "advice": ["Check that Docker Desktop and the extension's docker-proxy are running."]}
    result = assess(merge_samples(readings), profile=profile, app_memory_mib=app_memory_mib)
    body = result.to_dict()
    body.update(canCreate=result.verdict == "SUFFICIENT", advice=advice(result))
    return body


def _source_objects(gateway: KubernetesTestClusterGateway, namespace: str) -> dict[str, list[dict[str, Any]]]:
    base = f"/api/v1/namespaces/{namespace}"
    try:
        return {"deployments": gateway.list_raw("deployments", f"/apis/apps/v1/namespaces/{namespace}/deployments"),
                "services": gateway.list_raw("services", f"{base}/services"),
                "configmaps": gateway.list_raw("configmaps", f"{base}/configmaps"),
                "serviceaccounts": gateway.list_raw("serviceaccounts", f"{base}/serviceaccounts")}
    except ClusterAccessError as error:
        raise HTTPException(status_code=409, detail={"code": "SOURCE_UNREADABLE", "message": f"The application's cluster could not be read: {error}"}) from None


def _plan(body: PreviewBody, source: Callable[[], KubernetesTestClusterGateway]) -> PreparationPlan:
    try:
        if body.manual_yaml and body.manual_yaml.strip():
            return parse_manual(body.manual_yaml, body.target_namespace)
        objects = _source_objects(source(), body.namespace)
        return prepare(source_namespace=body.namespace, target_namespace=body.target_namespace, **objects)
    except PreparationRefused as error:
        raise HTTPException(status_code=422, detail={"code": error.code, "message": error.message}) from None


@router.post("/preview")
def preview(body: PreviewBody, source: Callable[[], KubernetesTestClusterGateway] = Depends(get_source_gateway)) -> dict[str, Any]:
    """What would be copied onto the disposable cluster, and what could not be. Reads the connected cluster; writes nothing anywhere."""
    return _plan(body, source).to_dict()


@router.post("/deploy")
def deploy(body: DeployBody, _armed: None = Depends(require_cluster_writes_armed),
           source: Callable[[], KubernetesTestClusterGateway] = Depends(get_source_gateway),
           target: Callable[[], KubernetesTestClusterGateway] = Depends(get_target_gateway)) -> dict[str, Any]:
    """Create the previewed objects on the disposable cluster. Needs the digest of the preview the person saw."""
    plan = _plan(body, source)
    if plan.digest != body.preview_digest:
        raise HTTPException(status_code=409, detail={"code": "PREVIEW_CHANGED", "message": "What would be copied changed since you reviewed it. Review the new preview."})
    if not plan.deployable:
        raise HTTPException(status_code=409, detail={"code": "COPY_NOT_DEPLOYABLE", "message": "Some things could not be copied and have to be resolved first: "
                            + "; ".join(f"{m.kind} {m.name}" for m in plan.missing if m.blocking)})
    destination = target()
    here, there = source().identity().cluster_uid, destination.identity().cluster_uid
    if not there or there == here:
        raise HTTPException(status_code=409, detail={"code": "TARGET_IS_NOT_A_SEPARATE_CLUSTER", "message": "The disposable cluster could not be told apart from "
                            "the cluster the application runs on, so nothing is created."})
    created, existing = [], []
    destination.ensure_namespace(plan.target_namespace)
    for manifest in plan.manifests:
        (created if destination.create_object(manifest, namespace=plan.target_namespace) else existing).append(f"{manifest['kind']}/{manifest['metadata']['name']}")
    return {"created": created, "alreadyExisted": existing, "namespace": plan.target_namespace, "previewDigest": plan.digest}
