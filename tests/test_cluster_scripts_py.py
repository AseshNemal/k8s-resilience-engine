"""scripts/cluster_up.py and scripts/extension_cluster_access.py, exercised in-process with a recording stand-in
for the shell. No Docker, kind, kubectl or cluster is needed, and nothing here is POSIX-specific, so these run on
macOS, Linux and Windows (the file-mode checks are skipped where the system has no Unix modes)."""

import importlib.util
import io
import os
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cluster_up = load("cluster_up")
access = load("extension_cluster_access")
Captured = sys.modules["_cli"].Captured

ADMIN_KUBECONFIG = "apiVersion: v1\nkind: Config\nclusters: [{name: kind-resilience-research}]\n# admin credentials\n"
CA = "Q0FEQVRB"
UNIX_MODES = os.name != "nt"


class FakeShell:
    """Answers reads from a function, records everything that would have run, never starts a process."""

    def __init__(self, tools=("docker", "kind", "kubectl"), answer=None, run_codes=None):
        self.tools = set(tools)
        self.answer = answer or (lambda text: Captured(0, ""))
        self.run_codes = run_codes or {}
        self.reads, self.ran = [], []

    def which(self, name):
        return f"/fake/{name}" if name in self.tools else None

    def capture(self, argv, cwd, timeout, show_errors=False):
        self.reads.append(list(argv))
        return self.answer(" ".join(argv))

    def run(self, argv, cwd):
        self.ran.append(list(argv))
        for prefix, code in self.run_codes.items():
            if " ".join(argv).startswith(prefix):
                return code
        return 0

    def commands(self, prefix=""):
        every = [" ".join(a) for a in self.reads + self.ran]
        return [c for c in every if c.startswith(prefix)]


class Clock:
    """A clock that only moves when the script sleeps, so a 120 s timeout takes no time."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


# ---- cluster_up -------------------------------------------------------------------------------------------

def make_repo(tmp_path):
    (tmp_path / "deploy" / "kind").mkdir(parents=True, exist_ok=True)
    (tmp_path / "deploy" / "kind" / "research-cluster.yaml").write_text("kind: Cluster\n")
    return tmp_path


def healthy(clusters="resilience-research\n", running="true", server="https://127.0.0.1:52838", nodes=None):
    nodes = nodes if nodes is not None else (
        "NAME STATUS ROLES AGE VERSION\n".splitlines()[:0]
        + [f"{n} Ready <none> 1d v1" for n in ("cp", "w1", "w2", "w3")]
    )

    def answer(text):
        if text.startswith("docker info"):
            return Captured(0, "ok")
        if text.startswith("kind get clusters"):
            return Captured(0, clusters)
        if text.startswith("kind get nodes"):
            return Captured(0, "resilience-research-control-plane\nresilience-research-worker\n")
        if text.startswith("docker inspect"):
            return Captured(0, running + "\n")
        if text.startswith("kind get kubeconfig"):
            return Captured(0, ADMIN_KUBECONFIG)
        if "get --raw=/readyz" in text:
            return Captured(0, "ok")
        if "get nodes --no-headers" in text:
            return Captured(0, "\n".join(nodes) + "\n")
        if "jsonpath={.clusters[0].cluster.server}" in text:
            return Captured(0, server)
        if "jsonpath={.clusters[0].name}" in text:
            return Captured(0, "kind-resilience-research")
        if "config view --raw --minify --flatten" in text:
            return Captured(0, ADMIN_KUBECONFIG)
        return Captured(0, "")

    return answer


def up(tmp_path, shell, environ=None, clock=None):
    clock = clock or Clock()
    return cluster_up.main([], shell=shell, root=make_repo(tmp_path), environ=environ or {},
                           sleep=clock.sleep, clock=clock)


def test_a_missing_tool_is_named(tmp_path, capsys):
    assert up(tmp_path, FakeShell(tools=("docker", "kubectl"))) == 1

    assert "'kind' is not installed" in capsys.readouterr().err


def test_a_stopped_docker_is_reported(tmp_path, capsys):
    shell = FakeShell(answer=lambda text: Captured(1, "") if text.startswith("docker info") else Captured(0, ""))

    assert up(tmp_path, shell) == 1

    assert "Start Docker Desktop" in capsys.readouterr().err
    assert shell.ran == []


def test_an_existing_cluster_is_preserved_and_stopped_nodes_are_started(tmp_path, capsys):
    shell = FakeShell(answer=healthy(running="false"))

    assert up(tmp_path, shell) == 0

    assert "preserving it" in capsys.readouterr().out
    assert shell.commands("docker start") == ["docker start resilience-research-control-plane",
                                              "docker start resilience-research-worker"]
    assert shell.commands("kind create") == []


def test_a_missing_cluster_is_created_from_the_repository_config(tmp_path):
    shell = FakeShell(answer=healthy(clusters=""))

    assert up(tmp_path, shell) == 0

    (create,) = shell.commands("kind create")
    assert create.startswith("kind create cluster --name resilience-research --config ")
    assert create.endswith("research-cluster.yaml")


def test_a_failed_create_stops_with_its_exit_code(tmp_path):
    shell = FakeShell(answer=healthy(clusters=""), run_codes={"kind create": 3})

    assert up(tmp_path, shell) == 3
    assert not (tmp_path / ".kube" / "kind-app.config").exists()


def test_the_cluster_name_can_be_chosen(tmp_path):
    shell = FakeShell(answer=healthy(clusters="other\n"))

    up(tmp_path, shell, environ={"KIND_CLUSTER_NAME": "other"})

    assert "kind get nodes --name other" in shell.commands("kind get nodes")


def test_an_api_server_that_never_answers_is_an_error_and_the_cluster_is_left_alone(tmp_path, capsys):
    def never_ready(text):
        return Captured(1, "") if "get --raw=/readyz" in text else healthy()(text)

    shell = FakeShell(answer=never_ready)
    clock = Clock()

    assert up(tmp_path, shell, environ={"CLUSTER_READY_TIMEOUT": "9"}, clock=clock) == 1

    err = capsys.readouterr().err
    assert "not reachable after 9s" in err and "left untouched" in err
    assert shell.commands("kind delete") == []
    assert clock.now >= 9  # it waited, using the injected clock, instead of failing at once


def test_the_app_kubeconfig_reaches_the_host_through_host_docker_internal_with_tls_still_verified(tmp_path):
    shell = FakeShell(answer=healthy())

    assert up(tmp_path, shell) == 0

    set_cluster = [c for c in shell.commands("kubectl") if "set-cluster" in c]
    assert len(set_cluster) == 1
    assert "--server=https://host.docker.internal:52838" in set_cluster[0]
    assert "--tls-server-name=localhost" in set_cluster[0]
    assert "insecure" not in set_cluster[0]
    final = tmp_path / ".kube" / "kind-app.config"
    assert final.read_text() == ADMIN_KUBECONFIG
    assert not (tmp_path / ".kube" / "kind-app.config.tmp").exists()


def test_the_admin_kubeconfig_is_never_left_behind(tmp_path):
    shell = FakeShell(answer=healthy())
    up(tmp_path, shell)

    used = {Path(part) for argv in shell.reads + shell.ran for part in argv
            if part.endswith(".config") and "kind-host-" in part}

    assert used and not any(path.exists() for path in used)


@pytest.mark.skipif(not UNIX_MODES, reason="Windows has no Unix file modes")
def test_the_app_kubeconfig_and_its_folder_are_private(tmp_path):
    up(tmp_path, FakeShell(answer=healthy()))

    assert stat.S_IMODE((tmp_path / ".kube" / "kind-app.config").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / ".kube").stat().st_mode) == 0o700


@pytest.mark.parametrize("server", ["https://10.0.0.5:6443", "http://127.0.0.1:6443", "https://127.0.0.1"])
def test_an_unexpected_api_address_is_refused(tmp_path, capsys, server):
    assert up(tmp_path, FakeShell(answer=healthy(server=server))) == 1

    assert "Unexpected API server address" in capsys.readouterr().err
    assert not (tmp_path / ".kube" / "kind-app.config").exists()


def test_a_wrong_node_count_is_a_warning_not_a_failure(tmp_path, capsys):
    assert up(tmp_path, FakeShell(answer=healthy(nodes=["cp Ready <none> 1d v1"]))) == 0

    assert "expected 4 Ready nodes" in capsys.readouterr().err


def test_help_and_unknown_arguments(capsys):
    assert cluster_up.main(["--help"]) == 0
    assert "kind research cluster" in capsys.readouterr().out
    assert cluster_up.main(["--nope"]) == 2


# ---- extension_cluster_access ----------------------------------------------------------------------------------

TOKEN = "eyJ-secret-token-value"


def access_answers(server="https://127.0.0.1:52838", ca=CA, token=TOKEN, ready=True):
    def answer(text):
        if "get --raw=/readyz" in text:
            return Captured(0 if ready else 1, "")
        if "jsonpath={.clusters[0].cluster.server}" in text:
            return Captured(0, server)
        if "certificate-authority-data" in text:
            return Captured(0, ca)
        if " create token " in text:
            return Captured(0, token + "\n")
        return Captured(0, "")

    return answer


def grant(tmp_path, *argv, shell=None, stdin="", environ=None):
    root = make_repo(tmp_path)
    (root / "deploy" / "rbac").mkdir(parents=True, exist_ok=True)
    (root / "deploy" / "rbac" / "resilience-readonly.yaml").write_text("kind: ClusterRole\n")
    shell = shell or FakeShell(answer=access_answers())
    code = access.main(list(argv), shell=shell, root=root, environ=environ or {}, stdin=io.StringIO(stdin))
    return code, shell, root / ".kube" / "extension-readonly.kubeconfig"


def test_a_missing_kubectl_is_named(tmp_path, capsys):
    code, _, _ = grant(tmp_path, "--yes", shell=FakeShell(tools=("docker",)))

    assert code == 1 and "'kubectl' is not installed" in capsys.readouterr().err


def test_an_unreachable_cluster_points_at_cluster_up(tmp_path, capsys):
    code, shell, _ = grant(tmp_path, "--yes", shell=FakeShell(answer=access_answers(ready=False)))

    assert code == 1
    assert "Run ./scripts/cluster-up.sh first" in capsys.readouterr().err
    assert shell.ran == []


def test_it_asks_first_and_an_empty_answer_changes_nothing(tmp_path, capsys):
    code, shell, out = grant(tmp_path, stdin="\n")

    assert code == 1
    assert "Aborted; nothing was changed." in capsys.readouterr().out
    assert shell.commands("kubectl --context kind-resilience-research apply") == [] and not out.exists()


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES"])
def test_a_yes_proceeds(tmp_path, answer):
    code, shell, out = grant(tmp_path, stdin=answer + "\n")

    assert code == 0 and out.exists()


def test_a_read_only_credential_is_written_for_the_host_with_tls_verified(tmp_path):
    code, shell, out = grant(tmp_path, "--yes", "--duration", "24h")

    assert code == 0
    text = out.read_text()
    assert "server: https://host.docker.internal:52838" in text
    assert "tls-server-name: localhost" in text
    assert f"certificate-authority-data: {CA}" in text
    assert f"token: {TOKEN}" in text
    assert "insecure" not in text and "client-key" not in text  # the admin credential is never copied
    assert any(c.endswith("--duration 24h") for c in shell.commands("kubectl")), shell.commands("kubectl")


def test_the_token_is_never_printed_or_put_on_a_command_line(tmp_path, capsys):
    code, shell, out = grant(tmp_path, "--yes")

    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert not any(TOKEN in part for argv in shell.reads + shell.ran for part in argv)


@pytest.mark.skipif(not UNIX_MODES, reason="Windows has no Unix file modes")
def test_the_credential_file_is_private(tmp_path):
    _, _, out = grant(tmp_path, "--yes")

    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert stat.S_IMODE(out.parent.stat().st_mode) == 0o700
    assert not out.with_name(out.name + ".tmp").exists()


def test_every_kubectl_call_names_the_kind_context_never_the_current_one(tmp_path):
    _, shell, _ = grant(tmp_path, "--yes")

    calls = [argv for argv in shell.reads + shell.ran if argv[0] == "kubectl"]
    assert calls and all(argv[1:3] == ["--context", "kind-resilience-research"] for argv in calls)


def test_the_cluster_name_can_be_chosen(tmp_path):
    _, shell, _ = grant(tmp_path, "--yes", environ={"KIND_CLUSTER_NAME": "other"})

    assert all(argv[2] == "kind-other" for argv in shell.reads + shell.ran if argv[0] == "kubectl")


@pytest.mark.parametrize("answers,message", [
    (dict(server="https://10.1.1.1:6443"), "Unexpected API server address"),
    (dict(ca=""), "No embedded certificate authority"),
    (dict(token=""), "Token creation failed"),
])
def test_unusable_cluster_details_stop_before_anything_is_written(tmp_path, capsys, answers, message):
    code, _, out = grant(tmp_path, "--yes", shell=FakeShell(answer=access_answers(**answers)))

    assert code == 1 and message in capsys.readouterr().err
    assert not out.exists()


def test_revoke_deletes_exactly_what_the_script_created_and_the_local_file(tmp_path, capsys):
    root = make_repo(tmp_path)
    (root / ".kube").mkdir()
    out = root / ".kube" / "extension-readonly.kubeconfig"
    out.write_text("old")

    code, shell, _ = grant(tmp_path, "--revoke", "--yes")

    assert code == 0 and not out.exists()
    (delete,) = shell.commands("kubectl --context kind-resilience-research delete")
    assert delete.endswith("clusterrolebinding/resilience-engine-reader clusterrole/resilience-engine-reader "
                           "namespace/resilience-engine --ignore-not-found")
    assert "Access revoked" in capsys.readouterr().out


def test_revoke_asks_first(tmp_path):
    code, shell, _ = grant(tmp_path, "--revoke", stdin="n\n")

    assert code == 1 and shell.commands("kubectl --context kind-resilience-research delete") == []


@pytest.mark.parametrize("argv", [["--duration"], ["--nope"]])
def test_usage_errors_exit_2_and_change_nothing(tmp_path, argv):
    code, shell, _ = grant(tmp_path, *argv)

    assert code == 2 and shell.ran == []


def test_the_prompt_text_names_what_is_granted(tmp_path, capsys):
    grant(tmp_path, stdin="n\n")

    out = capsys.readouterr().out
    assert "get/list on nodes, pods, services, deployments and replicasets" in out
    assert "Existing access objects are updated" in out
