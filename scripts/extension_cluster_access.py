#!/usr/bin/env python3
"""Explicit setup action for the Docker Desktop extension: create a READ-ONLY, time-limited credential for the
local kind cluster and write it to .kube/extension-readonly.kubeconfig. Pick that file in the extension's
"Connect cluster" page. It works the same on macOS, Linux and Windows (scripts/extension-cluster-access.sh or
scripts\\extension-cluster-access.cmd).

  extension-cluster-access                 create/renew (default token life: 168h)
  extension-cluster-access --duration 24h  shorter-lived token
  extension-cluster-access --revoke        remove all access created by this script
  add --yes to skip the confirmation prompt

This changes the cluster (a namespace, service account, cluster role and binding), so it only runs when you
invoke it, and it asks first. The app and the extension never run it.
Environment: KIND_CLUSTER_NAME (default resilience-research). Needs Python 3.9 or newer and kubectl on PATH.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cli import Exit, SystemShell, die, posix_only_chmod, write_private_file  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
READ_SECONDS = 60
NAMESPACE = "resilience-engine"
SERVICE_ACCOUNT = "resilience-engine-reader"


class ClusterAccess:
    def __init__(self, shell, root: Path, environ: dict, stdin, duration: str, revoke: bool, assume_yes: bool):
        self.shell, self.root, self.stdin = shell, root, stdin
        self.duration, self.revoke, self.assume_yes = duration, revoke, assume_yes
        name = environ.get("KIND_CLUSTER_NAME") or "resilience-research"
        self.context = f"kind-{name}"
        self.manifest = root / "deploy" / "rbac" / "resilience-readonly.yaml"
        self.kube_dir = root / ".kube"
        self.out_file = self.kube_dir / "extension-readonly.kubeconfig"

    # kubectl always targets the kind research cluster explicitly, never whatever context happens to be current.
    def kubectl(self, *args: str) -> list[str]:
        return ["kubectl", "--context", self.context, *args]

    def read(self, argv: list[str], show_errors: bool = False):
        return self.shell.capture(argv, self.root, READ_SECONDS, show_errors)

    def confirm(self) -> None:
        if self.assume_yes:
            return
        print("Proceed? [y/N] ", end="", flush=True)
        answer = (self.stdin.readline() or "").strip()
        if answer not in ("y", "Y", "yes", "YES"):
            print("Aborted; nothing was changed.")
            raise Exit(1)

    def check_ready(self) -> None:
        if not self.shell.which("kubectl"):
            die("'kubectl' is not installed or not on PATH.")
        if not self.manifest.is_file():
            die(f"Missing {self.manifest}")
        if self.read(self.kubectl("get", "--raw=/readyz", "--request-timeout=10s")).code != 0:
            die(f"Cannot reach cluster context '{self.context}'. Run ./scripts/cluster-up.sh first.")

    def do_revoke(self) -> None:
        print(f"""This will DELETE from cluster '{self.context}':
  namespace/{NAMESPACE} (and its service account), clusterrole/{NAMESPACE}-reader,
  clusterrolebinding/{NAMESPACE}-reader
All tokens issued for that service account stop working. The local file {self.out_file} is removed.""")
        self.confirm()
        code = self.shell.run(self.kubectl(
            "delete", f"clusterrolebinding/{NAMESPACE}-reader", f"clusterrole/{NAMESPACE}-reader",
            f"namespace/{NAMESPACE}", "--ignore-not-found"), self.root)
        if code != 0:
            raise Exit(code)
        self.out_file.unlink(missing_ok=True)
        print("Access revoked. Disconnect the cluster in the extension's Connect cluster page too.")

    def do_create(self) -> None:
        print(f"""This will CREATE in cluster '{self.context}' (see deploy/rbac/resilience-readonly.yaml):
  namespace/{NAMESPACE}, serviceaccount/{SERVICE_ACCOUNT},
  clusterrole + clusterrolebinding granting ONLY get/list on nodes, pods, services, deployments and replicasets.
It then issues a bearer token valid for {self.duration} and writes {self.out_file} (mode 600).
No workloads are deployed. Existing access objects are updated to match the manifest; no other objects are modified.""")
        self.confirm()

        if self.read(self.kubectl("apply", "-f", str(self.manifest)), show_errors=True).code != 0:
            die("Could not apply the read-only access objects.")
        print("Read-only access objects applied.")

        # Server address and CA come from the admin context, but the admin credential itself is NOT copied.
        server = self.read(self.kubectl("config", "view", "--minify", "-o",
                                        "jsonpath={.clusters[0].cluster.server}")).out.strip()
        ca_data = self.read(self.kubectl("config", "view", "--raw", "--minify", "-o",
                                         "jsonpath={.clusters[0].cluster.certificate-authority-data}")).out.strip()
        parsed = urlparse(server)
        if parsed.scheme != "https" or parsed.hostname not in ("127.0.0.1", "localhost") or not parsed.port:
            die(f"Unexpected API server address '{server}' for context '{self.context}'.")
        if not ca_data:
            die(f"No embedded certificate authority found for context '{self.context}'.")

        # The token is held in memory only; it is never printed or passed on a command line.
        token = self.read(self.kubectl("-n", NAMESPACE, "create", "token", SERVICE_ACCOUNT,
                                       "--duration", self.duration), show_errors=True).out.strip()
        if not token:
            die("Token creation failed.")

        self.kube_dir.mkdir(parents=True, exist_ok=True)
        posix_only_chmod(self.kube_dir, 0o700)
        staging = self.out_file.with_name(self.out_file.name + ".tmp")
        # The extension backend runs in the Docker Desktop VM and reaches the API server through the
        # port kind publishes on the host. The certificate is verified against the name it lists.
        text = f"""apiVersion: v1
kind: Config
clusters:
  - name: kind-readonly
    cluster:
      server: https://host.docker.internal:{parsed.port}
      tls-server-name: localhost
      certificate-authority-data: {ca_data}
users:
  - name: {SERVICE_ACCOUNT}
    user:
      token: {token}
contexts:
  - name: kind-readonly
    context:
      cluster: kind-readonly
      user: {SERVICE_ACCOUNT}
current-context: kind-readonly
"""
        try:
            write_private_file(staging, text)
            os.replace(str(staging), str(self.out_file))
        finally:
            if staging.exists():
                staging.unlink()

        print(f"""
Done. Read-only credential written to .kube/extension-readonly.kubeconfig (valid for {self.duration}).

Next: open the extension in Docker Desktop, go to "Connect cluster", and pick that file.

Renew:   re-run this script and pick the new file (older tokens stay valid until they expire).
Revoke:  ./scripts/extension-cluster-access.sh --revoke   (invalidates every token immediately)
         (Windows: scripts\\extension-cluster-access.cmd --revoke)
Note:    anyone who can read the file can read this cluster's nodes, pods, services, deployments and replicasets until
         it expires or is revoked. It cannot change anything or read secrets. Do not commit it.""")

    def run(self) -> None:
        self.check_ready()
        self.do_revoke() if self.revoke else self.do_create()


def main(argv: list[str] | None = None, *, shell=None, root: Path = REPO_ROOT, environ: dict | None = None,
         stdin=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    duration, revoke, assume_yes, index = "168h", False, False, 0
    while index < len(args):
        arg = args[index]
        if arg in ("-h", "--help"):
            print((__doc__ or "").strip("\n"))
            return 0
        if arg == "--duration":
            if index + 1 >= len(args) or not args[index + 1]:
                print("ERROR: --duration needs a value such as 24h", file=sys.stderr)
                return 2
            duration, index = args[index + 1], index + 1
        elif arg == "--revoke":
            revoke = True
        elif arg == "--yes":
            assume_yes = True
        else:
            print(f"ERROR: Unknown argument: {arg}", file=sys.stderr)
            return 2
        index += 1
    try:
        ClusterAccess(shell or SystemShell(), root, dict(os.environ if environ is None else environ),
                      stdin if stdin is not None else sys.stdin, duration, revoke, assume_yes).run()
    except Exit as stop:
        return stop.code
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
