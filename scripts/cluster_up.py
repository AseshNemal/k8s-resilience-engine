#!/usr/bin/env python3
"""Create (or reuse) the local kind research cluster and write a dedicated kubeconfig for the app container.

Run it before `docker compose up --build -d`, and again after Docker Desktop restarts. It works the same on
macOS, Linux and Windows (start it with scripts/cluster-up.sh or scripts\\cluster-up.cmd).

- Creates the cluster only if it does not exist; an existing cluster and its workloads are preserved
  (stopped node containers are started, never recreated).
- Succeeds only if the cluster's API server is actually reachable.
- Does not deploy workloads, run experiments, or change anything in the cluster.

Environment: KIND_CLUSTER_NAME (default resilience-research), CLUSTER_READY_TIMEOUT seconds (default 120).
Needs Python 3.9 or newer, and docker, kind and kubectl on PATH.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cli import Exit, SystemShell, die, posix_only_chmod, write_private_file  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NODES = 4  # 1 control plane + 3 workers
READ_SECONDS = 60


class ClusterUp:
    def __init__(self, shell, root: Path, environ: dict, sleep: Callable[[float], None], clock: Callable[[], float]):
        self.shell, self.root, self.sleep, self.clock = shell, root, sleep, clock
        self.name = environ.get("KIND_CLUSTER_NAME") or "resilience-research"
        self.timeout = float(environ.get("CLUSTER_READY_TIMEOUT") or 120)
        self.kind_config = root / "deploy" / "kind" / "research-cluster.yaml"
        self.kube_dir = root / ".kube"
        self.app_kubeconfig = self.kube_dir / "kind-app.config"

    # -- small helpers --------------------------------------------------------------------------------

    def read(self, argv: list[str], show_errors: bool = False):
        return self.shell.capture(argv, self.root, READ_SECONDS, show_errors)

    def kubectl(self, kubeconfig: Path, *args: str) -> list[str]:
        return ["kubectl", "--kubeconfig", str(kubeconfig), *args]

    # -- the steps ------------------------------------------------------------------------------------

    def check_prerequisites(self) -> None:
        for tool in ("docker", "kind", "kubectl"):
            if not self.shell.which(tool):
                die(f"'{tool}' is not installed or not on PATH (see docs/DOCKER.md).")
        if self.read(["docker", "info"]).code != 0:
            die("Cannot reach the Docker daemon. Start Docker Desktop and wait until it is running.")
        if not self.kind_config.is_file():
            die(f"Missing kind config: {self.kind_config}")

    def create_or_reuse(self) -> None:
        clusters = self.read(["kind", "get", "clusters"]).out.split()
        if self.name in clusters:
            print(f"Kind cluster '{self.name}' already exists; preserving it.")
            # Node containers stop when Docker Desktop restarts. Start them again (data is kept).
            for node in self.read(["kind", "get", "nodes", "--name", self.name]).out.split():
                running = self.read(["docker", "inspect", "-f", "{{.State.Running}}", node]).out.strip()
                if running != "true":
                    print(f"Starting stopped node container: {node}")
                    self.read(["docker", "start", node])
            return
        print(f"Creating kind cluster '{self.name}' (1 control-plane + 3 workers)...")
        code = self.shell.run(["kind", "create", "cluster", "--name", self.name, "--config", str(self.kind_config)],
                              self.root)
        if code != 0:
            raise Exit(code)

    def wait_until_reachable(self, host_kubeconfig: Path) -> None:
        print(f"Waiting up to {self.timeout:g}s for the Kubernetes API server...")
        deadline = self.clock() + self.timeout
        while True:
            config = self.read(["kind", "get", "kubeconfig", "--name", self.name])
            if config.code == 0 and config.out.strip():
                write_private_file(host_kubeconfig, config.out)
                ready = self.read(self.kubectl(host_kubeconfig, "get", "--raw=/readyz", "--request-timeout=5s"))
                if ready.code == 0:
                    return
            if self.clock() >= deadline:
                die(
                    f"Cluster '{self.name}' exists but its API server is not reachable after {self.timeout:g}s.\n"
                    f"  Check:  docker ps -a --filter name={self.name}\n"
                    f"  Logs:   docker logs {self.name}-control-plane\n"
                    "  The cluster was left untouched. If it is unrecoverable and holds nothing you need, you may\n"
                    f"  delete it yourself with 'kind delete cluster --name {self.name}' and re-run this script."
                )
            self.sleep(3)

    def report_nodes(self, host_kubeconfig: Path) -> None:
        print("API server is reachable:")
        self.shell.run(self.kubectl(host_kubeconfig, "get", "nodes"), self.root)
        rows = [line.split() for line in
                self.read(self.kubectl(host_kubeconfig, "get", "nodes", "--no-headers")).out.splitlines() if line.strip()]
        ready = sum(1 for row in rows if len(row) > 1 and row[1] == "Ready")
        if len(rows) != EXPECTED_NODES or ready != len(rows):
            print(f"WARNING: expected {EXPECTED_NODES} Ready nodes (1 control-plane + 3 workers); "
                  f"found {len(rows)}, {ready} Ready.", file=sys.stderr)

    def write_app_kubeconfig(self, host_kubeconfig: Path) -> None:
        # The app container is intentionally NOT attached to the 'kind' Docker network, so cluster nodes and pods
        # cannot resolve it by name. It reaches the API server through the port kind publishes on the host
        # instead: https://host.docker.internal:<port>. The API server certificate does not list that name, so
        # TLS is verified against the name it does list (tls-server-name=localhost). Certificate verification
        # stays on; the cluster CA is embedded in the file. Requires Docker Desktop, where
        # host.docker.internal reaches host loopback ports.
        server = self.read(self.kubectl(host_kubeconfig, "config", "view", "--minify", "-o",
                                        "jsonpath={.clusters[0].cluster.server}")).out.strip()
        entry = self.read(self.kubectl(host_kubeconfig, "config", "view", "--minify", "-o",
                                       "jsonpath={.clusters[0].name}")).out.strip()
        parsed = urlparse(server)
        if parsed.scheme != "https" or parsed.hostname not in ("127.0.0.1", "localhost") or not parsed.port:
            die(f"Unexpected API server address '{server}' in the kind kubeconfig.")

        self.kube_dir.mkdir(parents=True, exist_ok=True)
        posix_only_chmod(self.kube_dir, 0o700)
        flattened = self.read(self.kubectl(host_kubeconfig, "config", "view", "--raw", "--minify", "--flatten"),
                              show_errors=True)
        if flattened.code != 0 or not flattened.out.strip():
            die("Could not read the kind kubeconfig.")
        staging = self.app_kubeconfig.with_name(self.app_kubeconfig.name + ".tmp")
        try:
            write_private_file(staging, flattened.out)
            code = self.shell.run(
                self.kubectl(staging, "config", "set-cluster", entry,
                             f"--server=https://host.docker.internal:{parsed.port}", "--tls-server-name=localhost"),
                self.root,
            )
            if code != 0:
                die("Could not rewrite the cluster address in the app kubeconfig.")
            os.replace(str(staging), str(self.app_kubeconfig))
        finally:
            if staging.exists():
                staging.unlink()
        posix_only_chmod(self.app_kubeconfig, 0o600)

    def run(self) -> None:
        self.check_prerequisites()
        self.create_or_reuse()
        handle, name = tempfile.mkstemp(prefix="kind-host-", suffix=".config")
        os.close(handle)
        host_kubeconfig = Path(name)  # holds admin credentials: created private, deleted at the end
        try:
            self.wait_until_reachable(host_kubeconfig)
            self.report_nodes(host_kubeconfig)
            self.write_app_kubeconfig(host_kubeconfig)
        finally:
            host_kubeconfig.unlink(missing_ok=True)
        print(f"""
Cluster '{self.name}' is ready.
Kubeconfig for the app container: .kube/kind-app.config (mode 600 where the system has modes, git-ignored)

Next:  docker compose up --build -d      (if the app is already running: docker compose restart app)

Notes:
- That kubeconfig grants cluster-admin on this local test cluster. Mounting it read-only
  does not restrict what the credentials can do. Do not share or commit it.
- kind worker nodes are containers on one Docker host: logical failure domains, not
  independent physical machines.""")


def main(argv: list[str] | None = None, *, shell=None, root: Path = REPO_ROOT, environ: dict | None = None,
         sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        if args[0] in ("-h", "--help", "help"):
            print((__doc__ or "").strip("\n"))
            return 0
        print(f"Unknown argument: {args[0]}\n\n{(__doc__ or '').strip(chr(10))}", file=sys.stderr)
        return 2
    try:
        ClusterUp(shell or SystemShell(), root, dict(os.environ if environ is None else environ), sleep, clock).run()
    except Exit as stop:
        return stop.code
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
