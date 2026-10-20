"""The small Kubernetes REST client: kubeconfig loading, TLS, credentials, redirects.

TLS tests talk to a real local server over TLS with the certificates in tests/fixtures/tls. Those are
valid for the name ``localhost`` and for no IP address, exactly like kind's API certificate.
"""

import base64
import json
import os
import re
import ssl
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import certifi
import httpx
import pytest

from app.integrations.kubernetes import rest
from app.integrations.kubernetes.rest import (
    Connection,
    HttpStatusError,
    KubeconfigError,
    RestClient,
    TransportFailure,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tls"
CA = (FIXTURES / "ca.pem").read_bytes()
CLIENT_CERT = (FIXTURES / "client.pem").read_bytes()
CLIENT_KEY = (FIXTURES / "client.key").read_bytes()


def b64(data) -> str:
    return base64.b64encode(data if isinstance(data, bytes) else data.encode()).decode()


# ---------------------------------------------------------------- a real TLS server

class Seen:
    def __init__(self):
        self.requests = []  # (method, path, headers, client certificate common name)


@contextmanager
def tls_server(*, require_client_cert=False, redirect_to=None, body=None):
    seen = Seen()

    class Handler(BaseHTTPRequestHandler):
        def _reply(self):
            peer = self.connection.getpeercert()
            common_name = None
            if peer:
                common_name = dict(item[0] for item in peer["subject"]).get("commonName")
            length = int(self.headers.get("Content-Length") or 0)
            seen.requests.append((self.command, self.path, dict(self.headers), common_name, self.rfile.read(length)))
            if redirect_to:
                self.send_response(302)
                self.send_header("Location", redirect_to)
                self.end_headers()
                return
            payload = json.dumps(body if body is not None else {"items": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = _reply

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass  # the tests make handshakes fail on purpose

    server = Server(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(FIXTURES / "server.pem", FIXTURES / "server.key")
    if require_client_cert:
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cadata=CA.decode())
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def connect(server, **overrides):
    values = {"server": server, "ca_data": CA, "tls_server_name": "localhost", "token": "tok"}
    values.update(overrides)
    return Connection(**values)


def another_ca() -> bytes:
    """A real certificate that has nothing to do with the test server (the first one in certifi's bundle)."""
    bundle = Path(certifi.where()).read_text()
    return re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----\n", bundle, re.S).group(0).encode()


# ---------------------------------------------------------------- TLS

def test_tls_server_name_lets_an_ip_address_verify_against_the_name_the_certificate_has():
    with tls_server() as (server, seen):
        with RestClient(connect(server), 5) as http:
            assert http.get_json("/api/v1/nodes") == {"items": []}
    assert seen.requests[0][1] == "/api/v1/nodes"


def test_without_tls_server_name_the_same_server_fails_verification():
    with tls_server() as (server, _):
        with RestClient(connect(server, tls_server_name=None), 5) as http:
            with pytest.raises(TransportFailure) as caught:
                http.get_json("/api/v1/nodes")
    assert caught.value.name == "ConnectError"


def test_a_different_server_name_fails_verification():
    with tls_server() as (server, _):
        with RestClient(connect(server, tls_server_name="someone-else.example"), 5) as http:
            with pytest.raises(TransportFailure):
                http.get_json("/api/v1/nodes")


def test_a_server_not_signed_by_the_given_ca_is_refused():
    with tls_server() as (server, seen):
        with RestClient(connect(server, ca_data=another_ca()), 5) as http:
            with pytest.raises(TransportFailure):
                http.get_json("/api/v1/nodes")
    assert seen.requests == [], "no request, and so no credential, may reach an unverified server"


def test_without_any_ca_the_system_roots_are_used_and_the_private_test_ca_is_not_trusted():
    with tls_server() as (server, _):
        with RestClient(connect(server, ca_data=None), 5) as http:
            with pytest.raises(TransportFailure):
                http.get_json("/api/v1/nodes")


def test_a_ca_that_is_not_pem_is_a_kubeconfig_error_not_a_crash():
    with pytest.raises(KubeconfigError, match="certificate authority"):
        RestClient(connect("https://127.0.0.1:1", ca_data=b"not a certificate"), 5)


def test_the_strict_x509_flag_that_rejects_kubeadm_cas_is_off_but_verification_is_on():
    context = rest._ssl_context(connect("https://127.0.0.1:1"))
    assert not context.verify_flags & ssl.VERIFY_X509_STRICT
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


# ---------------------------------------------------------------- credentials

def test_a_client_certificate_is_presented_and_its_private_copy_is_deleted(monkeypatch):
    created = []
    real_mkdtemp = rest.tempfile.mkdtemp

    def spy(*args, **kwargs):
        folder = real_mkdtemp(*args, **kwargs)
        created.append(folder)
        return folder

    modes = {}
    real_load = ssl.SSLContext.load_cert_chain

    def watch(self, certfile, keyfile=None, *a, **k):
        modes["folder"] = oct(os.stat(os.path.dirname(certfile)).st_mode & 0o777)
        modes["key"] = oct(os.stat(keyfile).st_mode & 0o777)
        return real_load(self, certfile, keyfile, *a, **k)

    monkeypatch.setattr(rest.tempfile, "mkdtemp", spy)
    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", watch)

    with tls_server(require_client_cert=True) as (server, seen):
        with RestClient(connect(server, token=None, cert_data=CLIENT_CERT, key_data=CLIENT_KEY), 5) as http:
            http.get_json("/api/v1/nodes")
    assert seen.requests[0][3] == "kind-admin"
    assert "authorization" not in {k.lower() for k in seen.requests[0][2]}
    assert len(created) == 1 and not os.path.exists(created[0]), "the private copy of the key must be removed"
    if os.name == "posix":
        assert modes == {"folder": "0o700", "key": "0o600"}


def test_a_server_that_requires_a_client_certificate_rejects_a_client_without_one():
    with tls_server(require_client_cert=True) as (server, seen):
        with RestClient(connect(server, token=None), 5) as http:
            with pytest.raises(TransportFailure):
                http.get_json("/api/v1/nodes")
    assert seen.requests == []


def test_a_certificate_without_its_key_is_not_used_and_a_mismatched_pair_is_refused():
    with RestClient(connect("https://127.0.0.1:1", cert_data=CLIENT_CERT), 5):
        pass  # half a pair is ignored: there is nothing to present
    with pytest.raises(KubeconfigError):
        RestClient(connect("https://127.0.0.1:1", cert_data=CLIENT_CERT, key_data=(FIXTURES / "server.key").read_bytes()), 5)


def test_the_bearer_token_is_sent_only_when_there_is_one():
    with tls_server() as (server, seen):
        with RestClient(connect(server), 5) as http:
            http.get_json("/a")
        with RestClient(connect(server, token=None), 5) as http:
            http.get_json("/b")
    assert seen.requests[0][2]["Authorization"] == "Bearer tok"
    assert "Authorization" not in seen.requests[1][2]


def test_a_redirect_is_not_followed_so_the_credential_stays_with_the_named_server():
    with tls_server(body={"leak": "this server was reached"}) as (elsewhere, elsewhere_seen):
        with tls_server(redirect_to=elsewhere + "/stolen") as (server, _):
            with RestClient(connect(server), 5) as http:
                with pytest.raises(HttpStatusError) as caught:
                    http.get_json("/api/v1/nodes")
    assert caught.value.status == 302
    assert elsewhere_seen.requests == []


def test_the_environment_cannot_redirect_or_re_trust_a_credentialed_request(monkeypatch, tmp_path):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "bundle-that-does-not-exist"))
    monkeypatch.setenv("NETRC", str(tmp_path / "netrc"))
    (tmp_path / "netrc").write_text("machine 127.0.0.1 login attacker password secret\n")
    with tls_server() as (server, seen):
        with RestClient(connect(server), 5) as http:
            http.get_json("/api/v1/nodes")
    assert seen.requests[0][2]["Authorization"] == "Bearer tok"


def test_post_sends_a_json_body():
    with tls_server(body={"status": {"allowed": True}}) as (server, seen):
        with RestClient(connect(server), 5) as http:
            assert http.post_json("/apis/x", {"a": 1}) == {"status": {"allowed": True}}
    method, path, headers, _, sent = seen.requests[0]
    assert (method, path, json.loads(sent)) == ("POST", "/apis/x", {"a": 1})
    assert headers["Content-Type"] == "application/json"


def test_an_error_status_is_reported_without_the_response_body():
    def secret_body(request):
        return httpx.Response(500, text="database password is hunter2")

    with RestClient(connect("https://127.0.0.1:1"), 5, transport=httpx.MockTransport(secret_body)) as http:
        with pytest.raises(HttpStatusError) as caught:
            http.get_json("/x")
    assert "hunter2" not in str(caught.value) and caught.value.status == 500


def test_a_connection_record_never_prints_its_secrets():
    shown = repr(connect("https://h:1", token="tok-secret", cert_data=b"CERT-secret", key_data=b"KEY-secret"))
    assert "secret" not in shown


# ---------------------------------------------------------------- loading kubeconfigs

def kubeconfig(path, *, server="https://127.0.0.1:1", ca=None, user="{token: t}", current="c", extra=""):
    cluster = "{server: '%s'%s}" % (server, "" if ca is None else ", " + ca)
    path.write_text(
        (f"current-context: {current}\n" if current else "")
        + f"clusters:\n- name: k\n  cluster: {cluster}\nusers:\n- name: u\n  user: {user}\n"
        + "contexts:\n- name: c\n  context: {cluster: k, user: u}\n" + extra
    )
    return str(path)


def test_the_go_helper_credential_is_readable_by_the_backend():
    """The golden file is what the host helper writes; the backend must accept exactly that."""
    connection = rest.load_connection([str(FIXTURES / "backend-kubeconfig.yaml")])
    assert connection.server == "https://host.docker.internal:58227"
    assert connection.tls_server_name == "localhost"
    assert connection.ca_data == CA and connection.token == "golden-token-not-a-secret"
    assert connection.cert_data is None and connection.key_data is None


def test_the_go_helper_credential_connects_with_the_name_override_and_not_without(tmp_path):
    """End to end: the backend connects to an address that is not on the certificate, as it does for
    host.docker.internal, because the golden file's tls-server-name says which name to verify."""
    with tls_server() as (server, seen):
        document = (FIXTURES / "backend-kubeconfig.yaml").read_text().replace("https://host.docker.internal:58227", server)
        path = tmp_path / "from-helper"
        path.write_text(document)
        with RestClient(rest.load_connection([str(path)]), 5) as http:
            http.get_json("/api/v1/nodes")
        assert seen.requests[0][2]["Authorization"] == "Bearer golden-token-not-a-secret"

        path.write_text(re.sub(r"^\s*tls-server-name: localhost\n", "", document, flags=re.M))
        assert "tls-server-name" not in path.read_text()
        with RestClient(rest.load_connection([str(path)]), 5) as http:
            with pytest.raises(TransportFailure):
                http.get_json("/api/v1/nodes")


def test_relative_files_are_resolved_against_the_kubeconfig_that_names_them(tmp_path):
    (tmp_path / "certs").mkdir()
    (tmp_path / "certs" / "ca.crt").write_bytes(CA)
    (tmp_path / "certs" / "tok").write_text("file-token\n")
    path = kubeconfig(tmp_path / "config", ca="certificate-authority: certs/ca.crt", user="{tokenFile: certs/tok}")
    connection = rest.load_connection([path])
    assert connection.ca_data == CA and connection.token == "file-token"


def test_embedded_data_wins_over_a_file_and_bad_base64_is_refused(tmp_path):
    (tmp_path / "ca.crt").write_bytes(b"FROM-FILE")
    path = kubeconfig(tmp_path / "a", ca=f"certificate-authority: ca.crt, certificate-authority-data: {b64('EMBEDDED')}")
    assert rest.load_connection([path]).ca_data == b"EMBEDDED"
    with pytest.raises(KubeconfigError, match="base64"):
        rest.load_connection([kubeconfig(tmp_path / "b", ca="certificate-authority-data: '!!! not base64 !!!'")])


def test_the_first_file_that_defines_a_name_wins_and_the_first_current_context_is_used(tmp_path):
    first = tmp_path / "first"
    first.write_text(
        "current-context: from-first\nclusters: [{name: shared, cluster: {server: 'https://one:1'}}]\n"
        "users: [{name: u, user: {token: first-token}}]\ncontexts: [{name: from-first, context: {cluster: shared, user: u}}]\n"
    )
    second = tmp_path / "second"
    second.write_text(
        "current-context: from-second\nclusters: [{name: shared, cluster: {server: 'https://two:2'}}]\n"
        "users: [{name: u, user: {token: second-token}}]\n"
        "contexts: [{name: from-first, context: {cluster: shared, user: u}}, {name: from-second, context: {cluster: shared, user: u}}]\n"
    )
    connection = rest.load_connection([str(first), str(second)])
    assert (connection.server, connection.token) == ("https://one:1", "first-token")
    # Same files, other order: the other file now wins.
    other = rest.load_connection([str(second), str(first)])
    assert (other.server, other.token) == ("https://two:2", "second-token")


def test_current_context_falls_through_to_the_first_file_that_sets_one(tmp_path):
    first = tmp_path / "first"
    first.write_text("clusters: [{name: k, cluster: {server: 'https://h:1'}}]\nusers: [{name: u, user: {token: t}}]\n"
                     "contexts: [{name: c, context: {cluster: k, user: u}}]\n")
    second = tmp_path / "second"
    second.write_text("current-context: c\n")
    assert rest.load_connection([str(first), str(second)]).server == "https://h:1"


def test_each_relative_path_is_resolved_against_its_own_file(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "ca.crt").write_bytes(b"CA-A")
    (tmp_path / "b" / "ca.crt").write_bytes(b"CA-B")
    a = tmp_path / "a" / "config"
    a.write_text("current-context: x\nclusters: [{name: ca, cluster: {server: 'https://a:1', certificate-authority: ca.crt}}]\n"
                 "users: [{name: u, user: {token: t}}]\ncontexts: [{name: x, context: {cluster: ca, user: u}}]\n")
    b = tmp_path / "b" / "config"
    b.write_text("clusters: [{name: cb, cluster: {server: 'https://b:2', certificate-authority: ca.crt}}]\n"
                 "contexts: [{name: y, context: {cluster: cb, user: u}}]\n")
    assert rest.load_connection([str(a), str(b)]).ca_data == b"CA-A"
    b.write_text(b.read_text().replace("current-context: y", ""))
    b2 = tmp_path / "b" / "config2"
    b2.write_text("current-context: y\n" + b.read_text())
    assert rest.load_connection([str(b2), str(a)]).ca_data == b"CA-B"


@pytest.mark.parametrize(("user", "needle"), [
    ("{exec: {command: /bin/sh, args: ['-c', 'touch PLUGIN_RAN']}}", "credential plugin"),
    ("{auth-provider: {name: gcp}}", "credential plugin"),
    ("{username: admin, password: pw}", "basic authentication"),
])
def test_unsupported_credentials_are_refused_never_run(tmp_path, user, needle):
    marker = tmp_path / "PLUGIN_RAN"
    path = kubeconfig(tmp_path / "cfg", user=user.replace("PLUGIN_RAN", str(marker)))
    with pytest.raises(KubeconfigError, match=needle):
        rest.load_connection([path])
    assert not marker.exists()


@pytest.mark.parametrize(("ca", "needle"), [
    ("insecure-skip-tls-verify: true", "insecure-skip-tls-verify"),
    ("proxy-url: 'http://proxy:3128'", "proxy-url"),
])
def test_insecure_tls_and_proxies_are_refused(tmp_path, ca, needle):
    with pytest.raises(KubeconfigError, match=needle):
        rest.load_connection([kubeconfig(tmp_path / "cfg", ca=ca)])


@pytest.mark.parametrize("server", ["", "ftp://h:1", "h:1", "https://", None])
def test_a_missing_or_odd_server_address_is_refused(tmp_path, server):
    path = tmp_path / "cfg"
    path.write_text(
        "current-context: c\nclusters: [{name: k, cluster: {%s}}]\nusers: [{name: u, user: {token: t}}]\n"
        "contexts: [{name: c, context: {cluster: k, user: u}}]\n" % ("" if server is None else f"server: '{server}'")
    )
    with pytest.raises(KubeconfigError):
        rest.load_connection([str(path)])


def test_references_to_undefined_things_are_refused(tmp_path):
    path = tmp_path / "cfg"
    path.write_text("current-context: c\nclusters: []\nusers: []\ncontexts: [{name: c, context: {cluster: k, user: u}}]\n")
    with pytest.raises(KubeconfigError, match="cluster"):
        rest.load_connection([str(path)])
    path.write_text("current-context: c\nclusters: [{name: k, cluster: {server: 'https://h:1'}}]\nusers: []\n"
                    "contexts: [{name: c, context: {cluster: k, user: u}}]\n")
    with pytest.raises(KubeconfigError, match="user"):
        rest.load_connection([str(path)])
    path.write_text("current-context: nope\nclusters: []\ncontexts: []\n")
    with pytest.raises(KubeconfigError, match="current context"):
        rest.load_connection([str(path)])


def test_a_context_with_no_user_connects_anonymously(tmp_path):
    path = tmp_path / "cfg"
    path.write_text("current-context: c\nclusters: [{name: k, cluster: {server: 'https://h:1'}}]\n"
                    "contexts: [{name: c, context: {cluster: k}}]\n")
    assert rest.load_connection([str(path)]).token is None


@pytest.mark.parametrize("content", [b"- a\n- list\n", b"just text", b"this: [is not closed", b"\x00\x01binary"])
def test_files_that_are_not_kubeconfigs_are_refused_without_echoing_them(tmp_path, content):
    path = tmp_path / "cfg"
    path.write_bytes(content)
    with pytest.raises(KubeconfigError) as caught:
        rest.read_kubeconfig_file(str(path))
    assert content.decode("latin-1")[:6] not in str(caught.value) or len(content) < 6
    assert str(tmp_path) not in str(caught.value)


def test_a_missing_file_and_an_oversized_file_are_refused(tmp_path):
    with pytest.raises(KubeconfigError):
        rest.read_kubeconfig_file(str(tmp_path / "absent"))
    huge = tmp_path / "huge"
    huge.write_bytes(b"#" * (rest.MAX_KUBECONFIG_BYTES + 10))
    with pytest.raises(KubeconfigError, match="too large"):
        rest.read_kubeconfig_file(str(huge))


def test_a_token_file_that_cannot_be_read_is_refused_without_its_path(tmp_path):
    path = kubeconfig(tmp_path / "cfg", user="{tokenFile: nowhere/token}")
    with pytest.raises(KubeconfigError) as caught:
        rest.load_connection([path])
    assert "nowhere" not in str(caught.value)


def test_cluster_identity_ignores_credentials_and_reports_none_when_unknown(tmp_path):
    ok = kubeconfig(tmp_path / "ok", user="{exec: {command: x}}", ca="certificate-authority-data: AAAA")
    assert rest.cluster_identity([ok]) == ("https://127.0.0.1:1", "AAAA")
    assert rest.cluster_identity([str(tmp_path / "missing")]) is None
    assert rest.cluster_identity([]) is None
