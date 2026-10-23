"""Cluster connection endpoints for the Docker Desktop extension.

Extension-owned code, not part of any research component. It lets the developer hand the
backend a kubeconfig through the UI. The backend only accepts a self-contained, read-only
credential, stores a normalised copy at KUBECONFIG, and never logs or returns its contents.
"""

import base64
import contextlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

try:  # the backend runs in a Linux container; a developer machine without fcntl simply has no cross-process lock
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

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
# Developer-approved 2026-10-23: the credential is full access to the connected research cluster (Applying an
# approved change and resetting it write to it), so write access is no longer a reason to refuse it. It must
# still be able to read what the app reads.

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

# How long a disconnect waits for a connection attempt that is running before it says so.
DISCONNECT_WAIT_SECONDS = 20.0
SERVER = re.compile(r"^(?:[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?|\[[0-9A-Fa-f:]{2,45}\]:[0-9]{1,5})$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Identity(BaseModel):
    """What makes a cluster the one a person approved: its API address and certificate authority. Not secret."""

    server: str
    caSha256: str

    @field_validator("server")
    @classmethod
    def _valid_server(cls, value: str) -> str:
        if not SERVER.match(value):
            raise ValueError("invalid server")
        return value

    @field_validator("caSha256")
    @classmethod
    def _valid_fingerprint(cls, value: str) -> str:
        if not SHA256.match(value):
            raise ValueError("invalid fingerprint")
        return value


class ManagedInfo(BaseModel):
    """Non-secret facts about a credential the host helper installed (never the credential itself).

    ``origin`` says why it is being installed: ``authorize`` is a person's explicit approval, ``renew`` and
    ``reconnect`` ask nobody and are only accepted for the approved identity and never after a disconnect.
    Absent means an older helper, which is treated like a renewal that cannot record an identity.
    """

    context: str
    expiresAt: str | None = None
    origin: Literal["authorize", "renew", "reconnect"] | None = None
    identity: Identity | None = None

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


def _verify_access(document: dict) -> None:
    """Ask the cluster what this credential may do. Rejects a credential that cannot read what the app reads."""
    try:
        http = rest.RestClient(rest.connection_from_documents([(document, None)]), REQUEST_TIMEOUT_SECONDS)
    except rest.KubeconfigError as err:
        raise _bad(f"The kubeconfig cannot be used: {err}.")
    try:
        readable = all(_allowed(http, verb, resource, group) for verb, resource, group in REQUIRED_READS)
    except rest.HttpStatusError as err:
        if err.status in (401, 403):
            raise _bad("The cluster rejected the credential. It may have expired.", 401)
        raise _bad(f"The cluster returned HTTP {err.status} while checking the credential.", 502)
    except rest.TransportFailure as err:
        raise _bad(f"Could not reach the cluster API server ({err.name}).", 502)
    finally:
        http.close()

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
            # Only what the record always held: the origin and identity belong to approval.json, not here.
            json.dump(info.model_dump(include={"context", "expiresAt"}), handle)
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


# ---- the approved cluster, and whether the person disconnected it -------------------------------------------
#
# approval.json lives next to the credential in the data volume, so it survives page reloads, backend restarts
# and the helper's background job. It holds no credential: the context name, the cluster's API address, the
# fingerprint of its certificate authority, and whether the person disconnected it.


def _approval_path() -> Path:
    return KUBECONFIG_PATH.parent / "approval.json"


def _write_private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".record-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _read_approval() -> dict | None:
    try:
        data = json.loads(_approval_path().read_text(encoding="utf-8"))
        identity = Identity(server=data["server"], caSha256=data["caSha256"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not _valid_context(data.get("context")):
        return None
    return {
        "context": data["context"], "server": identity.server, "caSha256": identity.caSha256,
        "disconnectedByUser": data.get("disconnectedByUser") is True,
    }


def _write_approval(context: str, identity: Identity, *, disconnected: bool) -> None:
    _write_private_json(_approval_path(), {
        "context": context, "server": identity.server, "caSha256": identity.caSha256,
        "disconnectedByUser": disconnected, "recordedAt": datetime.now(timezone.utc).isoformat(),
    })


def _mark_disconnected() -> None:
    """The person disconnected: keep who was approved, but never reconnect or renew it without a new approval."""
    record = _read_approval()
    if record is not None and not record["disconnectedByUser"]:
        _write_approval(record["context"], Identity(server=record["server"], caSha256=record["caSha256"]), disconnected=True)


def _check_origin(managed: ManagedInfo) -> bool:
    """Refuse an install nobody asked for unless it is for the cluster the person approved and still wants.

    Returns True when the install should record the identity (a connection from before identities existed,
    renewed once). Raises 409 otherwise.
    """
    origin, record = managed.origin, _read_approval()
    if record is not None and record["disconnectedByUser"] and origin != "authorize":
        raise _bad("You disconnected this cluster, so it is not reconnected or renewed automatically. Connect it again to approve it.", 409)
    if origin in (None, "authorize"):
        return False
    if record is None:
        current = _read_managed()
        if origin == "renew" and KUBECONFIG_PATH.is_file() and current and current["context"] == managed.context:
            return managed.identity is not None
        raise _bad("No cluster has been approved, so nothing is renewed or reconnected without asking. Connect it first.", 409)
    different = managed.identity is not None and (
        managed.identity.server != record["server"] or managed.identity.caSha256 != record["caSha256"]
    )
    if record["context"] != managed.context or different:
        raise _bad("This is not the cluster you approved (its context, address or certificate authority differs), so nothing was changed.", 409)
    return False


@contextlib.contextmanager
def _connection_lock(wait: float):
    """One connection, renewal or disconnect at a time, across the server and the helper's ingest processes.

    A file lock in the data volume (both run in this container). ``wait`` is how long to wait for the running
    one; 0 refuses at once.
    """
    if fcntl is None:  # pragma: no cover
        yield
        return
    KUBECONFIG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = os.fdopen(os.open(KUBECONFIG_PATH.parent / ".connect.lock", os.O_CREAT | os.O_RDWR, 0o600), "r+")
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise _bad("Another connection or renewal is already running; try again shortly.", 409) from None
                time.sleep(0.05)
        yield
    finally:
        handle.close()  # closing releases the lock, whatever happened


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


def _approval_view() -> dict | None:
    record = _read_approval()
    if record is None:
        return None
    return {
        "context": record["context"], "server": record["server"], "caSha256": record["caSha256"],
        "disconnectedByUser": record["disconnectedByUser"],
    }


def _disconnected_status() -> dict:
    status: dict = {"connected": False}
    approved = _approval_view()
    if approved is not None:
        status["approved"] = approved
    return status


def _status() -> dict:
    status = _connected_status()
    approved = _approval_view()
    if approved is not None:
        status["approved"] = approved
    return status


def _connected_status() -> dict:
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
        if managed.origin == "reconnect" and managed.identity is None:
            raise _bad("A reconnect must carry the identity of the cluster that was approved.")
    with _connection_lock(wait=0):
        record_identity = managed is not None and _check_origin(managed)
        _verify_access(document)
        _store(document)
        _store_managed(managed)  # a manual upload clears any earlier managed record
        if managed is not None and managed.origin == "authorize":
            # The person just approved this cluster: remember it, and forget any earlier disconnect.
            if managed.identity is not None:
                _write_approval(managed.context, managed.identity, disconnected=False)
            else:
                _approval_path().unlink(missing_ok=True)
        elif record_identity:
            _write_approval(managed.context, managed.identity, disconnected=False)
        return _status()


@router.put("/cluster")
def connect_cluster(request: ConnectRequest) -> dict:
    return connect(request.kubeconfig, request.managed)


def disconnect() -> dict:
    """The person's disconnect: remove the credential and remember the choice, so nothing reconnects it."""
    with _connection_lock(wait=DISCONNECT_WAIT_SECONDS):
        KUBECONFIG_PATH.unlink(missing_ok=True)
        _managed_path().unlink(missing_ok=True)
        _mark_disconnected()
        return _status()


@router.delete("/cluster")
def disconnect_cluster() -> dict:
    return disconnect()
