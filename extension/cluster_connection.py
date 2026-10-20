"""Cluster connection endpoints for the Docker Desktop extension.

Extension-owned code, not part of any research component. It lets the developer hand the
backend a kubeconfig through the UI. The backend only accepts a self-contained, read-only
credential, stores a normalised copy at KUBECONFIG, and never logs or returns its contents.
"""

import base64
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.integrations.kubernetes import rest

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

# A credential installed by the host helper ("managed") is renewed automatically. The renewal policy
# lives here, in one place, and is computed from the token's real expiry claim: renew once
# min(RENEW_BEFORE, half of the token's actual lifetime) remains.
RENEW_BEFORE = timedelta(days=2)
MANAGED_SERVER_HOST = "host.docker.internal"
CONTEXT_NAME = re.compile(r"^[A-Za-z0-9._@:/-]{1,128}$")


def _valid_context(name: object) -> bool:
    """Kubeconfig context names are free text; accept only the safe characters real ones use, and
    never a path-traversal segment. The backend only stores and echoes the name, never opens it."""
    return isinstance(name, str) and bool(CONTEXT_NAME.match(name)) and ".." not in name

router = APIRouter(prefix="/_extension", tags=["Extension"])


class ManagedInfo(BaseModel):
    """Non-secret facts about a credential the host helper installed (never the credential itself)."""

    context: str
    expiresAt: str | None = None

    @field_validator("context")
    @classmethod
    def _safe_context(cls, value: str) -> str:
        if not _valid_context(value):
            raise ValueError("invalid context name")
        return value


class ConnectRequest(BaseModel):
    kubeconfig: str
    managed: ManagedInfo | None = None


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


SELF_SUBJECT_ACCESS_REVIEWS = "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews"


def _allowed(http: rest.RestClient, verb: str, resource: str, group: str) -> bool:
    review = {
        "apiVersion": "authorization.k8s.io/v1",
        "kind": "SelfSubjectAccessReview",
        "spec": {"resourceAttributes": {"verb": verb, "resource": resource, "group": group}},
    }
    result = http.post_json(SELF_SUBJECT_ACCESS_REVIEWS, review)
    status = result.get("status") if isinstance(result, dict) else None
    return isinstance(status, dict) and status.get("allowed") is True


def _verify_read_only(document: dict) -> None:
    """Ask the cluster what this credential may do. Rejects write access and missing read access."""
    try:
        http = rest.RestClient(rest.connection_from_documents([(document, None)]), REQUEST_TIMEOUT_SECONDS)
    except rest.KubeconfigError as err:
        raise _bad(f"The kubeconfig cannot be used: {err}.")
    try:
        writable = [f"{verb} {resource}" for verb, resource, group in FORBIDDEN_WRITES if _allowed(http, verb, resource, group)]
        readable = all(_allowed(http, verb, resource, group) for verb, resource, group in REQUIRED_READS)
    except rest.HttpStatusError as err:
        if err.status in (401, 403):
            raise _bad("The cluster rejected the credential. It may have expired.", 401)
        raise _bad(f"The cluster returned HTTP {err.status} while checking the credential.", 502)
    except rest.TransportFailure as err:
        raise _bad(f"Could not reach the cluster API server ({err.name}).", 502)
    finally:
        http.close()

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


def _managed_path() -> Path:
    return KUBECONFIG_PATH.parent / "managed.json"


def _store_managed(info: ManagedInfo | None) -> None:
    """Record that the helper manages this credential, or clear the record (manual connections)."""
    path = _managed_path()
    if info is None:
        path.unlink(missing_ok=True)
        return
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".managed-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(info.model_dump(), handle)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _read_managed() -> dict | None:
    try:
        data = json.loads(_managed_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and _valid_context(data.get("context")) else None
    except (OSError, ValueError):
        return None


def _token_claims(token: str) -> dict:
    """Read the claims of a JWT bearer token without verifying it. Returns {} if unreadable."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return claims if isinstance(claims, dict) else {}
    except (IndexError, ValueError, TypeError):
        return {}


def _epoch(claims: dict, name: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(claims[name]), tz=timezone.utc)
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        return None


def _token_expiry(token: str) -> datetime | None:
    return _epoch(_token_claims(token), "exp")


def _renew_at(expires: datetime, issued: datetime | None) -> datetime:
    """When to renew: RENEW_BEFORE ahead of expiry, or half way through a shorter-lived token."""
    lead = RENEW_BEFORE
    if issued is not None and expires > issued:
        lead = min(lead, (expires - issued) / 2)
    return expires - lead


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
    managed = _read_managed()
    status = {
        "connected": True,
        "server": f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname,
        "credential": "bearer token" if "token" in user else "client certificate",
        "expiresAt": None,
        "expired": False,
        "managed": managed is not None,
        "state": "ok",
    }
    if managed is not None:
        status["context"] = managed["context"]
    if "token" in user:
        claims = _token_claims(user["token"])
        expiry = _epoch(claims, "exp")
        if expiry is None and managed and managed.get("expiresAt"):
            # No readable exp claim: fall back to the expiry Kubernetes reported when it was issued.
            try:
                expiry = datetime.fromisoformat(str(managed["expiresAt"]).replace("Z", "+00:00"))
            except ValueError:
                expiry = None
        if expiry is not None:
            now = datetime.now(timezone.utc)
            status["expiresAt"] = expiry.isoformat().replace("+00:00", "Z")
            status["expired"] = expiry <= now
            if status["expired"]:
                status["state"] = "expired"
            elif managed is not None:
                renew_at = _renew_at(expiry, _epoch(claims, "iat"))
                status["renewAt"] = renew_at.isoformat().replace("+00:00", "Z")
                if renew_at <= now:
                    status["state"] = "renew-soon"
    return status


@router.get("/cluster")
def get_cluster_connection() -> dict:
    return _status()


def connect(kubeconfig: str, managed: ManagedInfo | None = None) -> dict:
    """Validate, store and report. Shared by the HTTP route and the helper's ingest command."""
    document = _normalise(kubeconfig)
    if managed is not None:
        # Only the host helper installs managed credentials, and it always points the backend at the
        # Docker Desktop host gateway with an embedded bearer token, so anything else is refused.
        cluster = document["clusters"][0]["cluster"]
        if urlsplit(cluster["server"]).hostname != MANAGED_SERVER_HOST:
            raise _bad(f"A managed connection must use {MANAGED_SERVER_HOST} as the API server.")
        if "token" not in document["users"][0]["user"]:
            raise _bad("A managed connection must use a bearer token.")
    _verify_read_only(document)
    _store(document)
    _store_managed(managed)  # a manual upload clears any earlier managed record
    return _status()


@router.put("/cluster")
def connect_cluster(request: ConnectRequest) -> dict:
    return connect(request.kubeconfig, request.managed)


@router.delete("/cluster")
def disconnect_cluster() -> dict:
    KUBECONFIG_PATH.unlink(missing_ok=True)
    _managed_path().unlink(missing_ok=True)
    return _status()
