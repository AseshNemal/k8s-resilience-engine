"""Cluster connection endpoints for the Docker Desktop extension.

Extension-owned code, not part of any research component. It lets the developer hand the
backend a kubeconfig through the UI. The backend only accepts a self-contained, read-only
credential, stores a normalised copy at KUBECONFIG, and never logs or returns its contents.
"""

import base64
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from fastapi import APIRouter, HTTPException
from kubernetes import client, config
from pydantic import BaseModel

KUBECONFIG_PATH = Path(os.environ.get("KUBECONFIG", "/data/kube/config"))
MAX_KUBECONFIG_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 8

# (verb, resource, api group). The credential must be allowed all of the first group...
REQUIRED_READS = [
    ("list", "nodes", ""),
    ("list", "pods", ""),
    ("list", "deployments", "apps"),
]
# ...and none of the second. These are checks that expose a credential able to change the cluster.
FORBIDDEN_WRITES = [
    ("create", "pods", ""),
    ("delete", "pods", ""),
    ("patch", "deployments", "apps"),
    ("create", "deployments", "apps"),
    ("delete", "nodes", ""),
    ("get", "secrets", ""),
    ("create", "clusterrolebindings", "rbac.authorization.k8s.io"),
]

router = APIRouter(prefix="/_extension", tags=["Extension"])


class ConnectRequest(BaseModel):
    kubeconfig: str


def _bad(detail: str, status: int = 422) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def _normalise(text: str) -> dict:
    """Reduce an uploaded kubeconfig to the few fields we accept, or reject it.

    Error messages never echo file content. Anything that could read local files, run a
    program, route through a proxy or weaken TLS is rejected rather than stripped.
    """
    if len(text.encode("utf-8")) > MAX_KUBECONFIG_BYTES:
        raise _bad("The kubeconfig is too large.")
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        raise _bad("The file is not valid YAML.")
    if not isinstance(document, dict):
        raise _bad("The file is not a kubeconfig.")

    contexts = {c.get("name"): c.get("context") or {} for c in document.get("contexts") or [] if isinstance(c, dict)}
    current = document.get("current-context")
    if current not in contexts:
        if len(contexts) != 1:
            raise _bad("The kubeconfig needs a current-context.")
        current = next(iter(contexts))
    context = contexts[current]

    clusters = {c.get("name"): c.get("cluster") or {} for c in document.get("clusters") or [] if isinstance(c, dict)}
    users = {u.get("name"): u.get("user") or {} for u in document.get("users") or [] if isinstance(u, dict)}
    cluster = clusters.get(context.get("cluster"))
    user = users.get(context.get("user"))
    if cluster is None or user is None:
        raise _bad("The current context does not reference a cluster and a user.")

    server = str(cluster.get("server") or "")
    if urlsplit(server).scheme != "https" or not urlsplit(server).hostname:
        raise _bad("The cluster server must be an https URL.")
    if cluster.get("insecure-skip-tls-verify"):
        raise _bad("Disabling TLS verification is not allowed.")
    if cluster.get("proxy-url"):
        raise _bad("A proxy-url is not allowed.")
    if not cluster.get("certificate-authority-data"):
        raise _bad("The cluster needs an embedded certificate-authority-data.")

    safe_cluster = {
        "server": server,
        "certificate-authority-data": str(cluster["certificate-authority-data"]),
    }
    if cluster.get("tls-server-name"):
        safe_cluster["tls-server-name"] = str(cluster["tls-server-name"])

    if user.get("exec") or user.get("auth-provider") or user.get("tokenFile") or user.get("username"):
        raise _bad("Only embedded bearer tokens or client certificates are accepted.")
    if user.get("token"):
        safe_user = {"token": str(user["token"])}
    elif user.get("client-certificate-data") and user.get("client-key-data"):
        safe_user = {
            "client-certificate-data": str(user["client-certificate-data"]),
            "client-key-data": str(user["client-key-data"]),
        }
    else:
        raise _bad("Only embedded bearer tokens or client certificates are accepted.")

    return {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "cluster", "cluster": safe_cluster}],
        "users": [{"name": "user", "user": safe_user}],
        "contexts": [{"name": "context", "context": {"cluster": "cluster", "user": "user"}}],
        "current-context": "context",
    }


def _allowed(authz: client.AuthorizationV1Api, verb: str, resource: str, group: str) -> bool:
    review = client.V1SelfSubjectAccessReview(
        spec=client.V1SelfSubjectAccessReviewSpec(
            resource_attributes=client.V1ResourceAttributes(verb=verb, resource=resource, group=group)
        )
    )
    result = authz.create_self_subject_access_review(review, _request_timeout=REQUEST_TIMEOUT_SECONDS)
    return bool(result.status and result.status.allowed)


def _verify_read_only(document: dict) -> None:
    """Ask the cluster what this credential may do. Rejects write access and missing read access."""
    api_client = config.new_client_from_config_dict(document)
    authz = client.AuthorizationV1Api(api_client)
    try:
        writable = [f"{verb} {resource}" for verb, resource, group in FORBIDDEN_WRITES if _allowed(authz, verb, resource, group)]
        readable = all(_allowed(authz, verb, resource, group) for verb, resource, group in REQUIRED_READS)
    except client.ApiException as err:
        if err.status in (401, 403):
            raise _bad("The cluster rejected the credential. It may have expired.", 401)
        raise _bad(f"The cluster returned HTTP {err.status} while checking the credential.", 502)
    except Exception as err:
        raise _bad(f"Could not reach the cluster API server ({type(err).__name__}).", 502)
    finally:
        api_client.close()

    if writable:
        raise _bad(
            "This credential can change the cluster (" + ", ".join(writable[:3])
            + "). Use scripts/extension-cluster-access.sh to create a read-only credential."
        )
    if not readable:
        raise _bad("This credential cannot list nodes, pods and deployments, which the app needs.")


def _store(document: dict) -> None:
    KUBECONFIG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=KUBECONFIG_PATH.parent, prefix=".kubeconfig-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, default_flow_style=False)
        os.chmod(temporary, 0o600)
        os.replace(temporary, KUBECONFIG_PATH)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _token_expiry(token: str) -> datetime | None:
    """Read the exp claim of a JWT bearer token without verifying it. Returns None if absent."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    except (IndexError, KeyError, ValueError, TypeError):
        return None


def _status() -> dict:
    if not KUBECONFIG_PATH.is_file():
        return {"connected": False}
    try:
        document = yaml.safe_load(KUBECONFIG_PATH.read_text(encoding="utf-8"))
        cluster = document["clusters"][0]["cluster"]
        user = document["users"][0]["user"]
    except (OSError, yaml.YAMLError, KeyError, IndexError, TypeError):
        return {"connected": False}

    parsed = urlsplit(cluster["server"])
    status = {
        "connected": True,
        "server": f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname,
        "credential": "bearer token" if "token" in user else "client certificate",
        "expiresAt": None,
        "expired": False,
    }
    if "token" in user:
        expiry = _token_expiry(user["token"])
        if expiry is not None:
            status["expiresAt"] = expiry.isoformat().replace("+00:00", "Z")
            status["expired"] = expiry <= datetime.now(timezone.utc)
    return status


@router.get("/cluster")
def get_cluster_connection() -> dict:
    return _status()


@router.put("/cluster")
def connect_cluster(request: ConnectRequest) -> dict:
    document = _normalise(request.kubeconfig)
    _verify_read_only(document)
    _store(document)
    return _status()


@router.delete("/cluster")
def disconnect_cluster() -> dict:
    KUBECONFIG_PATH.unlink(missing_ok=True)
    return _status()
