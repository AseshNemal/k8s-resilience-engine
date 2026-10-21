"""Read-only Kubernetes REST access over httpx, replacing the heavyweight ``kubernetes`` package.

The product only ever *reads* five collections and asks one authorization question, so a small
client is enough: it loads the parts of a kubeconfig that a local kind or Docker Desktop cluster uses
and speaks JSON to the API server. Rules that keep it safe:

* TLS is always verified against the cluster's CA; ``insecure-skip-tls-verify`` is refused, as it is by
  the extension's upload and by the host helper. ``tls-server-name`` is honoured, because kind's API
  certificate lists ``localhost`` but not the ``host.docker.internal`` name the extension connects by.
* Credential plugins (``exec``, ``auth-provider``), basic authentication and proxies are refused, never
  run or silently ignored. Running a program named in a file the app does not control is not allowed.
* The client does not read ``~/.netrc``, proxy variables or ``SSL_CERT_FILE`` (``trust_env=False``) and
  never follows a redirect, so a credential is sent only to the server the kubeconfig names.
* Messages carry no kubeconfig content, path or credential.
"""

import base64
import binascii
import os
import shutil
import ssl
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib.parse import urlsplit

import httpx
import yaml

MAX_KUBECONFIG_BYTES = 1024 * 1024


class KubeconfigError(Exception):
    """The kubeconfig cannot be used. The message names the problem; it is safe to show to the user."""


class HttpStatusError(Exception):
    """The API server answered with an error status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"the API server returned HTTP {status}")
        self.status = status


class TransportFailure(Exception):
    """The API server could not be reached or its answer could not be read (network, DNS, TLS, timeout)."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


@dataclass(frozen=True)
class Connection:
    """Everything needed to talk to one API server. Secrets are excluded from ``repr``."""

    server: str
    tls_server_name: str | None = None
    ca_data: bytes | None = None
    cert_data: bytes | None = field(default=None, repr=False)
    key_data: bytes | None = field(default=None, repr=False)
    token: str | None = field(default=None, repr=False)
    # The CA exactly as the kubeconfig names it (embedded data or a path), for the connection fingerprint.
    ca_identity: str = ""


# ---- reading kubeconfigs -------------------------------------------------------------------------


def read_kubeconfig_file(path: str) -> dict:
    """Parse one kubeconfig file. Every read of a kubeconfig from disk goes through here, which is also
    what the test suite guards so a test can never read the developer's real ``~/.kube``."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_KUBECONFIG_BYTES + 1)
    except OSError:
        raise KubeconfigError("the kubeconfig file cannot be read") from None
    if len(data) > MAX_KUBECONFIG_BYTES:
        raise KubeconfigError("the kubeconfig file is too large")
    try:
        document = yaml.safe_load(data)
    except yaml.YAMLError:
        raise KubeconfigError("the kubeconfig file is not valid YAML") from None
    if not isinstance(document, dict):
        raise KubeconfigError("the kubeconfig file is not a kubeconfig")
    return document


class _Merged:
    """Several kubeconfig documents merged as kubectl does: the first document that defines a name wins,
    and the first that sets ``current-context`` chooses it. Relative paths are resolved against the
    folder of the document that named them."""

    def __init__(self, documents: list[tuple[dict, str | None]]) -> None:
        self.current: str | None = None
        self.clusters: dict[str, tuple[dict, str | None]] = {}
        self.users: dict[str, tuple[dict, str | None]] = {}
        self.contexts: dict[str, dict] = {}
        for document, base in documents:
            if self.current is None and isinstance(document.get("current-context"), str):
                self.current = document["current-context"] or None
            self._add(self.clusters, document.get("clusters"), "cluster", base)
            self._add(self.users, document.get("users"), "user", base)
            for entry in document.get("contexts") or []:
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    self.contexts.setdefault(entry["name"], entry.get("context") or {})

    @staticmethod
    def _add(target: dict, entries: Any, key: str, base: str | None) -> None:
        for entry in entries or []:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                body = entry.get(key)
                target.setdefault(entry["name"], (body if isinstance(body, dict) else {}, base))

    def cluster(self) -> tuple[dict, str | None]:
        if not self.current or self.current not in self.contexts:
            raise KubeconfigError("the kubeconfig has no current context")
        context = self.contexts[self.current]
        if not isinstance(context, dict) or context.get("cluster") not in self.clusters:
            raise KubeconfigError("the current context names a cluster that is not defined")
        return self.clusters[context["cluster"]]

    def user(self) -> tuple[dict, str | None]:
        context = self.contexts[self.current]
        name = context.get("user")
        if name is None:
            return {}, None
        if name not in self.users:
            raise KubeconfigError("the current context names a user that is not defined")
        return self.users[name]


def _resolve(path: str, base: str | None) -> str:
    path = os.path.expanduser(path)
    return path if os.path.isabs(path) or base is None else os.path.join(base, path)


def _data_or_file(entry: dict, data_key: str, file_key: str, base: str | None, what: str) -> bytes | None:
    """Embedded base64 wins over a file, as in kubectl."""
    encoded = entry.get(data_key)
    if encoded:
        try:
            return base64.b64decode(str(encoded), validate=True)
        except (binascii.Error, ValueError):
            raise KubeconfigError(f"the {what} in the kubeconfig is not valid base64") from None
    path = entry.get(file_key)
    if path:
        try:
            with open(_resolve(str(path), base), "rb") as handle:
                return handle.read()
        except OSError:
            raise KubeconfigError(f"the {what} file cannot be read") from None
    return None


def _build_connection(merged: _Merged) -> Connection:
    cluster, cluster_base = merged.cluster()
    user, user_base = merged.user()

    server = cluster.get("server")
    parts = urlsplit(server) if isinstance(server, str) else None
    if not parts or parts.scheme not in ("http", "https") or not parts.hostname:
        raise KubeconfigError("the cluster has no usable API server address")
    if cluster.get("insecure-skip-tls-verify"):
        raise KubeconfigError("insecure-skip-tls-verify is not supported: TLS is always verified")
    if cluster.get("proxy-url"):
        raise KubeconfigError("a proxy-url in the kubeconfig is not supported")
    if user.get("exec") is not None or user.get("auth-provider") is not None:
        raise KubeconfigError("a credential plugin (exec or auth-provider) is not supported, and is never run")
    if user.get("username") or user.get("password"):
        raise KubeconfigError("basic authentication is not supported")

    token = user.get("token")
    if not token and user.get("tokenFile"):
        try:
            with open(_resolve(str(user["tokenFile"]), user_base), encoding="utf-8") as handle:
                token = handle.read().strip()
        except OSError:
            raise KubeconfigError("the token file cannot be read") from None

    ca_identity = cluster.get("certificate-authority-data") or cluster.get("certificate-authority") or ""
    tls_name = cluster.get("tls-server-name")
    return Connection(
        server=server.rstrip("/"),
        tls_server_name=str(tls_name) if tls_name else None,
        ca_data=_data_or_file(cluster, "certificate-authority-data", "certificate-authority", cluster_base, "certificate authority"),
        cert_data=_data_or_file(user, "client-certificate-data", "client-certificate", user_base, "client certificate"),
        key_data=_data_or_file(user, "client-key-data", "client-key", user_base, "client key"),
        token=str(token) if token else None,
        ca_identity=str(ca_identity),
    )


def connection_from_documents(documents: list[tuple[dict, str | None]]) -> Connection:
    """The connection described by the current context of already-parsed kubeconfig documents."""
    return _build_connection(_Merged(documents))


def load_connection(paths: list[str]) -> Connection:
    """The connection described by the current context of the merged kubeconfig files at ``paths``."""
    return connection_from_documents([(read_kubeconfig_file(p), os.path.dirname(os.path.abspath(p))) for p in paths])


def cluster_identity(paths: list[str]) -> tuple[str, str] | None:
    """(server, CA as named in the kubeconfig) of the current context's cluster, or None when unknown.

    Unlike ``load_connection`` this does not look at credentials, so a context whose credential this
    client cannot use still has an identity."""
    try:
        merged = _Merged([(read_kubeconfig_file(p), os.path.dirname(os.path.abspath(p))) for p in paths])
        cluster, _ = merged.cluster()
    except KubeconfigError:
        return None
    server = cluster.get("server")
    if not server or not isinstance(server, str):
        return None
    return server, str(cluster.get("certificate-authority-data") or cluster.get("certificate-authority") or "")


# ---- talking to the API server --------------------------------------------------------------------


@contextmanager
def _private_files(connection: Connection) -> Iterator[tuple[str | None, str | None]]:
    """Client certificate and key as files, for ``load_cert_chain``, in a private folder that is removed
    as soon as the TLS context holds them."""
    if not (connection.cert_data and connection.key_data):
        yield None, None
        return
    folder = tempfile.mkdtemp(prefix="kube-")
    try:
        os.chmod(folder, stat.S_IRWXU)
        paths = []
        for name, data in (("client.crt", connection.cert_data), ("client.key", connection.key_data)):
            path = os.path.join(folder, name)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            paths.append(path)
        yield paths[0], paths[1]
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _ssl_context(connection: Connection) -> ssl.SSLContext:
    try:
        if connection.ca_data:
            context = ssl.create_default_context(cadata=connection.ca_data.decode("ascii"))
        else:
            context = ssl.create_default_context()
        # Python 3.13 turns on strict X.509 checking, which rejects the CA certificates kubeadm and kind
        # generate (no authority key identifier). The chain and the host name are still verified.
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        with _private_files(connection) as (cert, key):
            if cert and key:
                context.load_cert_chain(cert, key)
    except (ssl.SSLError, UnicodeDecodeError, ValueError):
        raise KubeconfigError("the certificate authority or client certificate is not valid") from None
    except OSError:
        raise KubeconfigError("the client certificate cannot be used") from None
    return context


class RestClient:
    """GET and POST JSON to one API server. Build it, use it, close it."""

    def __init__(self, connection: Connection, timeout: float, *, transport: httpx.BaseTransport | None = None) -> None:
        self._server_name = connection.tls_server_name
        headers = {"Accept": "application/json"}
        if connection.token:
            headers["Authorization"] = f"Bearer {connection.token}"
        self._http = httpx.Client(
            base_url=connection.server,
            headers=headers,
            timeout=timeout,
            verify=_ssl_context(connection) if urlsplit(connection.server).scheme == "https" else True,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        )

    def __enter__(self) -> "RestClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def get_json(self, path: str) -> Any:
        return self._request("GET", path)

    def post_json(self, path: str, body: dict) -> Any:
        return self._request("POST", path, body)

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        # tls-server-name is the name the certificate is checked against, sent as the TLS server name.
        extensions = {"sni_hostname": self._server_name} if self._server_name else None
        try:
            response = self._http.request(method, path, json=body, extensions=extensions)
        except (httpx.TransportError, ssl.SSLError, OSError) as error:
            raise TransportFailure(type(error).__name__) from None
        if not 200 <= response.status_code < 300:
            raise HttpStatusError(response.status_code)
        try:
            return response.json()
        except ValueError:
            raise TransportFailure("InvalidResponse") from None
