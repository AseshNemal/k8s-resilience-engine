"""The Docker Engine proxy: the policy that authorizes CONTAINERS, the proxy that applies it, and the runner that uses it."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.integrations.docker.engine import DockerEngineFacts, DockerEngineRunner
from app.integrations.docker.policy import decide
from app.integrations.docker.proxy_app import allowed_clusters_from_env, create_app, trim_inspection
from app.integrations.kind.recovery import DockerCli

CLUSTERS = frozenset({"resilience-validation"})

WORKER = {"io.x-k8s.kind.cluster": "resilience-validation", "io.x-k8s.kind.role": "worker"}
CONTROL = {"io.x-k8s.kind.cluster": "resilience-validation", "io.x-k8s.kind.role": "control-plane"}
DESKTOP_WORKER = {"io.x-k8s.kind.cluster": "desktop", "io.x-k8s.kind.role": "worker"}
LABELS = {"resilience-validation-worker": WORKER, "resilience-validation-worker2": WORKER,
          "resilience-validation-control-plane": CONTROL, "desktop-worker": DESKTOP_WORKER,
          "resilience-validation-worker3": {"io.x-k8s.kind.cluster": "someone-else", "io.x-k8s.kind.role": "worker"},
          "postgres": {"app": "db"}}


def labels_of(name):
    return LABELS.get(name)


def verdict(method, path, query="", **options):
    return decide(method, path, query, labels_of, allowed_clusters=options.pop("allowed", CLUSTERS))


# --- the policy --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/_ping", "/info", "/containers/json", "/v1.45/info"])
def test_the_read_only_facts_the_assessment_needs_are_allowed(path):
    assert verdict("GET", path).allowed is True


def test_a_worker_of_an_allowed_cluster_can_be_inspected_killed_with_sigkill_and_started():
    assert verdict("GET", "/containers/resilience-validation-worker/json").allowed
    assert verdict("POST", "/containers/resilience-validation-worker/kill", "signal=SIGKILL").allowed
    assert verdict("POST", "/v1.45/containers/resilience-validation-worker2/start").allowed


@pytest.mark.parametrize("name", ["resilience-validation-control-plane", "postgres", "desktop-worker",
                                  "resilience-validation-worker3", "no-such-container"])
def test_a_container_that_is_not_an_authorized_worker_cannot_be_killed_whatever_its_name_suggests(name):
    decision = verdict("POST", f"/containers/{name}/kill", "signal=SIGKILL")

    assert decision.allowed is False


def test_the_name_alone_is_not_authorization_the_daemons_labels_must_agree():
    """A container named like a worker but labelled for another cluster is refused: the labels are the daemon's, not the caller's."""
    assert verdict("POST", "/containers/resilience-validation-worker3/kill", "signal=SIGKILL").allowed is False


def test_the_docker_desktop_research_cluster_is_reachable_only_when_the_proxy_was_deliberately_configured_for_it():
    assert verdict("POST", "/containers/desktop-worker/kill", "signal=SIGKILL").allowed is False
    assert verdict("POST", "/containers/desktop-worker/kill", "signal=SIGKILL",
                   allowed=frozenset({"resilience-validation", "desktop"})).allowed is True


@pytest.mark.parametrize("query", ["", "signal=SIGTERM", "signal=SIGKILL&t=0", "signal=SIGKILL&signal=SIGTERM", "t=1"])
def test_only_a_bare_sigkill_is_a_node_failure(query):
    assert verdict("POST", "/containers/resilience-validation-worker/kill", query).allowed is False


@pytest.mark.parametrize("method, path", [
    ("POST", "/containers/resilience-validation-worker/stop"), ("DELETE", "/containers/resilience-validation-worker"),
    ("POST", "/containers/resilience-validation-worker/exec"), ("POST", "/containers/create"),
    ("POST", "/containers/resilience-validation-worker/pause"), ("POST", "/containers/resilience-validation-worker/restart"),
    ("GET", "/containers/resilience-validation-worker/archive"), ("GET", "/containers/resilience-validation-worker/logs"),
    ("GET", "/volumes"), ("GET", "/images/json"), ("POST", "/networks/create"), ("GET", "/events"),
    ("POST", "/build"), ("GET", "/containers/postgres/json"), ("GET", "/containers/resilience-validation-worker/kill"),
    ("GET", "/containers/../etc/passwd/json"), ("GET", "/containers/%2e%2e/json")])
def test_everything_else_is_refused(method, path):
    assert verdict(method, path).allowed is False


def test_stats_are_read_once_never_streamed():
    assert verdict("GET", "/containers/postgres/stats", "stream=false&one-shot=true").allowed is True
    assert verdict("GET", "/containers/postgres/stats").allowed is False
    assert verdict("GET", "/containers/postgres/stats", "stream=true").allowed is False


def test_start_and_inspect_take_no_options():
    assert verdict("POST", "/containers/resilience-validation-worker/start", "detachKeys=x").allowed is False
    assert verdict("GET", "/containers/resilience-validation-worker/json", "size=1").allowed is False


# --- the proxy -----------------------------------------------------------------------------------------------------

class FakeDaemon:
    """A scripted Docker daemon behind the proxy; records exactly what reached it."""

    def __init__(self):
        self.received = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.received.append((request.method, request.url.path, request.url.query.decode()))
        parts = request.url.path.split("/")
        if request.url.path == "/info":
            return httpx.Response(200, json={"MemTotal": 4_000_000_000, "NCPU": 5, "ServerVersion": "29", "ContainersRunning": 6})
        if request.url.path == "/containers/json":
            return httpx.Response(200, json=[{"Names": ["/resilience-validation-worker"]}, {"Names": ["/postgres"]}])
        if len(parts) == 4 and parts[1] == "containers" and parts[3] == "json":
            labels = LABELS.get(parts[2])
            if labels is None:
                return httpx.Response(404, json={"message": f"No such container: {parts[2]}"})
            return httpx.Response(200, json={"Id": "abc", "Name": "/" + parts[2], "Config": {"Labels": labels, "Env": ["PASSWORD=hunter2"]},
                                             "State": {"Running": True, "Status": "running"},
                                             "HostConfig": {"RestartPolicy": {"Name": "on-failure"}, "Binds": ["/secret:/x"]},
                                             "Mounts": [{"Source": "/secret"}]})
        if parts[-1] in ("kill", "start"):
            return httpx.Response(204)
        if parts[-1] == "stats":
            return httpx.Response(200, json={"memory_stats": {"usage": 600, "stats": {"inactive_file": 100}}})
        return httpx.Response(404, json={"message": "unexpected"})


@pytest.fixture
def daemon():
    return FakeDaemon()


@pytest.fixture
def proxy(daemon):
    return TestClient(create_app(transport=httpx.MockTransport(daemon), allowed_clusters=CLUSTERS))


def mutating(daemon):
    return [r for r in daemon.received if r[0] != "GET"]


def test_a_refused_request_gets_a_403_with_the_reason_and_never_reaches_the_daemon(proxy, daemon):
    answer = proxy.post("/containers/postgres/kill", params={"signal": "SIGKILL"})

    assert answer.status_code == 403 and "worker" in answer.json()["message"]
    assert mutating(daemon) == []


def test_an_authorized_kill_is_forwarded_as_given(proxy, daemon):
    answer = proxy.post("/containers/resilience-validation-worker/kill", params={"signal": "SIGKILL"})

    assert answer.status_code == 204
    assert ("POST", "/containers/resilience-validation-worker/kill", "signal=SIGKILL") in daemon.received


def test_an_inspection_is_trimmed_so_no_environment_or_mount_leaves_the_proxy(proxy):
    body = proxy.get("/containers/resilience-validation-worker/json").json()

    assert body["Config"] == {"Labels": WORKER} and body["State"]["Running"] is True
    assert body["HostConfig"] == {"RestartPolicy": {"Name": "on-failure"}}
    assert "Env" not in json.dumps(body) and "Mounts" not in body and "hunter2" not in json.dumps(body)


def test_a_daemon_that_does_not_answer_fails_closed_for_authorization_and_is_a_502_for_a_read(daemon):
    def down(request):
        raise httpx.ConnectError("socket gone")

    app = TestClient(create_app(transport=httpx.MockTransport(down), allowed_clusters=CLUSTERS))

    assert app.post("/containers/resilience-validation-worker/kill", params={"signal": "SIGKILL"}).status_code == 403
    assert app.get("/info").status_code == 502


def test_the_allowed_clusters_come_from_the_proxys_own_environment_and_default_to_the_validation_cluster_only():
    assert allowed_clusters_from_env({}) == {"resilience-validation"}
    assert allowed_clusters_from_env({"RESILIENCE_PROXY_ALLOWED_CLUSTERS": " Resilience-Validation , desktop "}) == {
        "resilience-validation", "desktop"}


def test_trim_inspection_keeps_only_run_relevant_fields():
    assert set(trim_inspection({"Id": "x", "Config": {"Env": ["A=1"]}, "Mounts": [1]})) == {"Id", "Name", "Config", "State", "HostConfig"}


# --- the runner, end to end through the proxy -----------------------------------------------------------------------

class ThroughProxy(httpx.BaseTransport):
    """Sends the runner's requests into the proxy app, as the Unix socket would."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        answer = self._client.request(request.method, request.url.raw_path.decode())
        return httpx.Response(answer.status_code, content=answer.content, headers={"content-type": "application/json"})


@pytest.fixture
def runner(proxy):
    return DockerEngineRunner(transport=ThroughProxy(proxy))


def test_the_runner_satisfies_the_docker_cli_contract_the_injector_and_recovery_use(runner):
    cli = DockerCli(runner)

    info = cli.inspect("resilience-validation-worker")
    assert info == {"labels": WORKER, "running": True}
    assert cli.is_kind_worker_of("resilience-validation-worker", "resilience-validation") == (info, None)
    assert cli.run("kill", "resilience-validation-worker").returncode == 0
    assert cli.run("start", "resilience-validation-worker").returncode == 0


def test_the_runner_cannot_do_what_the_proxy_would_refuse_and_reports_it_as_a_failure(runner):
    assert DockerCli(runner).run("kill", "postgres").returncode == 1
    assert DockerCli(runner).inspect("postgres") is None


@pytest.mark.parametrize("args", [["docker", "stop", "x"], ["docker", "exec", "x"], ["docker", "kill"], ["rm", "-rf", "x"],
                                  ["docker", "kill", "../x"], ["docker", "kill", "a b"]])
def test_the_runner_refuses_anything_but_inspect_kill_start_on_a_plain_name_before_any_request(args):
    def never(request):
        raise AssertionError("a request was made")

    result = DockerEngineRunner(transport=httpx.MockTransport(never))(args)

    assert result.returncode == 2 and "refused" in result.stderr


def test_the_runner_is_a_test_double_unless_it_talks_to_a_real_socket(runner):
    assert runner.evidence_class == "TEST_DOUBLE"
    assert DockerEngineRunner(socket_path="/nonexistent.sock").evidence_class == "REAL_CLUSTER"


def test_an_unreachable_proxy_is_a_failed_command_not_an_exception():
    def down(request):
        raise httpx.ConnectError("no socket")

    result = DockerEngineRunner(transport=httpx.MockTransport(down))(["docker", "kill", "resilience-validation-worker"])

    assert result.returncode == 1 and "could not be reached" in result.stderr


def test_the_facts_for_the_memory_assessment_come_from_read_only_endpoints(proxy, daemon):
    facts = DockerEngineFacts(transport=ThroughProxy(proxy))

    assert facts.info()["memTotalBytes"] == 4_000_000_000
    assert facts.container_memory() == {"resilience-validation-worker": 500, "postgres": 500}
    assert mutating(daemon) == []
